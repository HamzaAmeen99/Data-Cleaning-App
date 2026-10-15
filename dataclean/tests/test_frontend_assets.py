"""Check frontend delivery without caching session data or private files."""

import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app
from config import Config


class FrontendAssetsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)

        class TestConfig(Config):
            TESTING = True
            SESSION_FILE_DIR = root / "sessions"
            UPLOAD_FOLDER = root / "uploads"
            PROCESSED_FOLDER = root / "processed"

        self.app = create_app(TestConfig)
        self.client = self.app.test_client()
        self.page = self.client.get("/").get_data(as_text=True)
        self.js = re.search(r'<script src="([^"]+)"', self.page)[1]

    def test_styles_and_fonts_need_no_external_stylesheet(self):
        self.assertNotIn('rel="stylesheet"', self.page)
        self.assertNotIn("fonts.googleapis.com", self.page)
        css = re.search(r"<style>(.*?)</style>", self.page, re.S)[1]
        self.assertNotIn("/*", css)
        self.assertIn("font-display:swap", css)
        self.assertNotIn("font-display:block", css)
        for url in set(re.findall(r"/assets/[^\s)\"']+\.woff2", css)):
            with self.client.get(url) as response:
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.data.startswith(b"wOF2"))

    def test_versioned_assets_cache_without_cookies(self):
        for client in (self.client, self.app.test_client()):
            response = client.get(self.js)
            self.assertEqual(response.status_code, 200)
            self.assertIn("max-age=31536000", response.headers["Cache-Control"])
            self.assertIn("immutable", response.headers["Cache-Control"])
            self.assertNotIn("Set-Cookie", response.headers)
            self.assertNotIn("Cookie", response.headers.get("Vary", ""))
            cached = client.get(self.js, headers={"If-None-Match": response.headers["ETag"]})
            self.assertEqual(cached.status_code, 304)
            self.assertIn("immutable", cached.headers["Cache-Control"])
            response.close()
            cached.close()

    def test_private_files_and_stale_versions_are_not_assets(self):
        version = self.js.split("/")[2]
        (self.app.config["UPLOAD_FOLDER"] / "private.csv").write_text("private")
        for path in (
            "/assets/old-version/js/app.js",
            f"/assets/{version}/../uploads/private.csv",
            f"/assets/{version}/uploads/private.csv",
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 404)
            self.assertNotIn("immutable", response.headers.get("Cache-Control", ""))
        self.assertNotIn("immutable", self.client.get("/").headers.get("Cache-Control", ""))


if __name__ == "__main__":
    unittest.main()
