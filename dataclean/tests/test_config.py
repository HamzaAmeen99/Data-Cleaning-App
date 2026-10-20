"""Environment-boundary checks for application startup."""

import io
import os
import sys
import unittest
import warnings
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from cachelib import FileSystemCache
from flask_session.cachelib import CacheLibSessionInterface

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app
from config import DevelopmentConfig, TestingConfig, config_for_environment


class EnvironmentConfigTest(unittest.TestCase):
    def test_explicit_environments_are_selected(self):
        self.assertIs(config_for_environment("development"), DevelopmentConfig)
        self.assertIs(config_for_environment("testing"), TestingConfig)

    def test_unknown_environment_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "DATACLEAN_ENV must be one of"):
            config_for_environment("staging")

    def test_production_requires_and_uses_environment_secret(self):
        environment = {"DATACLEAN_ENV": "production"}
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(RuntimeError, "DATACLEAN_SECRET_KEY must be set"):
                create_app()

        environment["DATACLEAN_SECRET_KEY"] = "too-short"
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(RuntimeError, "at least 32 bytes"):
                create_app()

        environment["DATACLEAN_SECRET_KEY"] = "a" * 64
        with patch.dict(os.environ, environment, clear=True):
            app = create_app()
        self.assertEqual(app.config["APP_ENV"], "production")
        self.assertEqual(app.config["SECRET_KEY"], environment["DATACLEAN_SECRET_KEY"])

    def test_production_session_cookie_has_secure_host_only_attributes(self):
        environment = {
            "DATACLEAN_ENV": "production",
            "DATACLEAN_SECRET_KEY": "a" * 64,
        }
        with patch.dict(os.environ, environment, clear=True):
            app = create_app()
        cookie = app.test_client().get(
            "/upload", base_url="localhost:5000"
        ).headers["Set-Cookie"]

        self.assertTrue(cookie.startswith("__Host-dataclean="), cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn("Path=/", cookie)
        self.assertNotIn("Domain=", cookie)

    def test_testing_cookie_remains_available_over_http(self):
        app = create_app(TestingConfig)
        cookie = app.test_client().get("/upload").headers["Set-Cookie"]

        self.assertTrue(cookie.startswith("session="), cookie)
        self.assertNotIn("Secure", cookie)

    def test_sessions_use_supported_cachelib_storage(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            app = create_app(TestingConfig)

        self.assertEqual(app.config["SESSION_TYPE"], "cachelib")
        self.assertNotIn("SESSION_FILE_DIR", app.config)
        self.assertIsInstance(app.config["SESSION_CACHELIB"], FileSystemCache)
        self.assertIsInstance(app.session_interface.delegate, CacheLibSessionInterface)
        self.assertFalse(
            any("deprecated" in str(item.message).lower() for item in caught),
            [str(item.message) for item in caught],
        )

    def test_session_lifetime_matches_file_retention_without_get_refresh(self):
        app = create_app(TestingConfig)
        client = app.test_client()

        first_response = client.get("/upload")
        second_response = client.get("/upload")

        self.assertTrue(app.config["SESSION_PERMANENT"])
        self.assertEqual(app.config["PERMANENT_SESSION_LIFETIME"], timedelta(hours=24))
        self.assertEqual(
            app.config["PERMANENT_SESSION_LIFETIME"].total_seconds(),
            app.config["FILE_TTL_SECONDS"],
        )
        self.assertFalse(app.config["SESSION_REFRESH_EACH_REQUEST"])
        self.assertIn("Expires=", first_response.headers["Set-Cookie"])
        self.assertNotIn("Set-Cookie", second_response.headers)

    def test_missing_workspace_has_expiry_recovery_message(self):
        app = create_app(TestingConfig)
        response = app.test_client().get("/dataset", follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"workspace is unavailable or has expired", response.data)

    def test_landing_page_does_not_create_a_session(self):
        app = create_app(TestingConfig)
        client = app.test_client()

        response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Set-Cookie", response.headers)
        self.assertIsNone(client.get_cookie(app.config["SESSION_COOKIE_NAME"]))

    def test_upload_page_starts_a_csrf_backed_session(self):
        app = create_app(TestingConfig)
        client = app.test_client()

        response = client.get("/upload")

        self.assertIn("Set-Cookie", response.headers)
        with client.session_transaction() as active_session:
            self.assertEqual(len(active_session["csrf_token"]), 64)

    def test_workspace_responses_prevent_private_data_caching(self):
        app = create_app(TestingConfig)
        client = app.test_client()

        for path in ("/upload", "/dataset", "/dataset/analysis", "/download"):
            response = client.get(path)
            self.assertEqual(response.headers["Cache-Control"], "no-store, private")
            self.assertEqual(response.headers["Pragma"], "no-cache")

    def test_public_landing_page_keeps_its_normal_cache_policy(self):
        response = create_app(TestingConfig).test_client().get("/")

        self.assertNotIn("no-store", response.headers.get("Cache-Control", ""))
        self.assertNotIn("Pragma", response.headers)

    def test_browser_security_headers_cover_public_and_private_pages(self):
        app = create_app(TestingConfig)
        client = app.test_client()

        for path in ("/", "/upload", "/dataset"):
            response = client.get(path)
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")
            self.assertEqual(response.headers["Referrer-Policy"], "same-origin")
            self.assertEqual(
                response.headers["Content-Security-Policy"],
                "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
            )
            self.assertNotIn("Strict-Transport-Security", response.headers)

    def test_production_enforces_https_for_future_visits(self):
        environment = {
            "DATACLEAN_ENV": "production",
            "DATACLEAN_SECRET_KEY": "a" * 64,
        }
        with patch.dict(os.environ, environment, clear=True):
            app = create_app()

        response = app.test_client().get(
            "/", base_url="https://hamzaameen.pythonanywhere.com"
        )
        self.assertEqual(
            response.headers["Strict-Transport-Security"],
            "max-age=31536000",
        )

    def test_every_mutation_endpoint_is_centrally_csrf_protected(self):
        app = create_app(TestingConfig)

        @app.route("/test-mutation", methods=["POST", "PUT", "PATCH", "DELETE"])
        def test_mutation():
            return "", 204

        client = app.test_client()
        client.get("/upload")
        with client.session_transaction() as active_session:
            csrf_token = active_session["csrf_token"]
        mutation_paths = (
            "/upload",
            "/session/end",
            "/clean",
            "/recipe/inspect",
            "/recipe/apply",
            "/dataset/preview",
            "/dataset/undo",
            "/dataset/reset",
            "/dataset/select-columns",
        )

        with self.assertLogs(level="WARNING"):
            for path in mutation_paths:
                self.assertEqual(client.post(path).status_code, 403, path)
                self.assertEqual(
                    client.post(path, data={"csrf_token": "incorrect"}).status_code,
                    403,
                    path,
                )
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                self.assertEqual(
                    client.open("/test-mutation", method=method).status_code,
                    403,
                    method,
                )
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            self.assertEqual(
                client.open(
                    "/test-mutation",
                    method=method,
                    data={"csrf_token": csrf_token},
                ).status_code,
                204,
                method,
            )
        self.assertEqual(client.post("/not-a-route").status_code, 404)

    def test_accepted_upload_rotates_session_and_csrf(self):
        app = create_app(TestingConfig)
        client = app.test_client()
        client.get("/upload")
        old_cookie = client.get_cookie(app.config["SESSION_COOKIE_NAME"]).value
        with client.session_transaction() as active_session:
            old_csrf = active_session["csrf_token"]

        response = client.post(
            "/upload",
            data={
                "file": (io.BytesIO(b"name,score\nAlice,10\n"), "rotation.csv"),
                "csrf_token": old_csrf,
            },
            content_type="multipart/form-data",
        )

        new_cookie = client.get_cookie(app.config["SESSION_COOKIE_NAME"]).value
        with client.session_transaction() as active_session:
            new_csrf = active_session["csrf_token"]
            self.assertEqual(active_session["original_filename"], "rotation.csv")
            session_paths = {Path(active_session["baseline_path"])}
            self.assertTrue(all(path.exists() for path in session_paths))
        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(new_cookie, old_cookie)
        self.assertNotEqual(new_csrf, old_csrf)
        self.assertEqual(
            client.post("/dataset/reset", data={"csrf_token": old_csrf}).status_code,
            403,
        )
        self.assertEqual(
            client.post("/dataset/reset", data={"csrf_token": new_csrf}).status_code,
            302,
        )

        stale_client = app.test_client()
        stale_client.set_cookie(app.config["SESSION_COOKIE_NAME"], old_cookie)
        stale_response = stale_client.get("/dataset", follow_redirects=True)
        self.assertNotIn(b"Column Information", stale_response.data)

        end_response = client.post(
            "/session/end",
            data={"csrf_token": new_csrf, "beacon": "1"},
        )
        self.assertEqual(end_response.status_code, 204)
        self.assertTrue(all(not path.exists() for path in session_paths))

    def test_rejected_upload_keeps_session_identity_and_workspace(self):
        app = create_app(TestingConfig)
        client = app.test_client()
        client.get("/upload")
        with client.session_transaction() as active_session:
            first_csrf = active_session["csrf_token"]
        client.post(
            "/upload",
            data={
                "file": (io.BytesIO(b"name\nAlice\n"), "accepted.csv"),
                "csrf_token": first_csrf,
            },
            content_type="multipart/form-data",
        )
        accepted_cookie = client.get_cookie(app.config["SESSION_COOKIE_NAME"]).value
        with client.session_transaction() as active_session:
            accepted_csrf = active_session["csrf_token"]
            accepted_path = active_session["current_path"]

        response = client.post(
            "/upload",
            data={
                "file": (io.BytesIO(b"not a dataset"), "rejected.txt"),
                "csrf_token": accepted_csrf,
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            client.get_cookie(app.config["SESSION_COOKIE_NAME"]).value,
            accepted_cookie,
        )
        with client.session_transaction() as active_session:
            self.assertEqual(active_session["csrf_token"], accepted_csrf)
            self.assertEqual(active_session["current_path"], accepted_path)
        client.post("/session/end", data={"csrf_token": accepted_csrf})


if __name__ == "__main__":
    unittest.main()
