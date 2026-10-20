// DataClean OS — Client-side interactions and UI helpers

document.addEventListener("DOMContentLoaded", () => {
  setupUploadFeedback();
  setupCustomValueToggle();
  setupTableSearch();
  setupCategoryMapping();
  setupMultiColumnConversion();
  setupValidationRules();
  setupDestructiveConfirm();
  setupActiveOperationsCount();
  setupPreviewBinding();
  setupFlashAutoDismiss();
  setupThemeToggle();
  setupMobileNavigation();
  setupSessionEnding();
  setupLoadingScreen();
  setupLandingReveals();
});

function setupLandingReveals() {
  const items = document.querySelectorAll("[data-reveal]");
  if (!items.length || !window.IntersectionObserver || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    items.forEach((item) => item.classList.add("is-visible"));
    return;
  }

  document.documentElement.classList.add("reveal-ready");
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add("is-visible");
      observer.unobserve(entry.target);
    });
  }, { threshold: 0.12, rootMargin: "0px 0px -36px" });

  items.forEach((item) => observer.observe(item));
}

function setupPreviewBinding() {
  const form = document.getElementById("cleaning-form");
  const dirty = form?.querySelector('input[name="preview_dirty"]');
  if (!form || !dirty) return;
  const markDirty = (event) => {
    if (event.target.matches('input[type="hidden"]')) return;
    if (event.target.matches("input, select, textarea") && !event.target.name) return;
    dirty.value = "1";
  };
  form.addEventListener("change", markDirty);
  form.addEventListener("input", markDirty);
}

function setupSessionEnding() {
  if (document.body.dataset.sessionActive !== "true") return;

  const modal = document.getElementById("end-session-modal");
  const openButton = document.getElementById("end-session-btn");
  const closeButton = document.getElementById("end-session-close");
  const cancelButton = document.getElementById("end-session-cancel");

  const openModal = () => {
    if (!modal) return;
    modal.classList.remove("hidden");
    if (cancelButton) cancelButton.focus();
  };

  const closeModal = () => {
    if (!modal) return;
    modal.classList.add("hidden");
    if (openButton) openButton.focus();
  };

  if (openButton) openButton.addEventListener("click", openModal);
  if (closeButton) closeButton.addEventListener("click", closeModal);
  if (cancelButton) cancelButton.addEventListener("click", closeModal);
  if (modal) {
    modal.addEventListener("click", (event) => {
      if (event.target === modal) closeModal();
    });
  }
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && modal && !modal.classList.contains("hidden")) {
      closeModal();
    }
  });

  const navigation = window.performance?.getEntriesByType?.("navigation")[0];
  if (navigation?.type === "reload") {
    // Let the workspace paint before the blocking browser confirmation opens.
    window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
      if (!window.confirm(
        "Refreshing ends this DataClean session and deletes its files. Continue?"
      )) return;

      const formData = new FormData();
      formData.append("csrf_token", document.body.dataset.csrfToken || "");
      formData.append("beacon", "1");
      fetch(document.body.dataset.endSessionUrl, {
        method: "POST",
        body: formData,
        credentials: "same-origin",
      }).then(() => {
        window.location.replace(document.body.dataset.uploadUrl);
      }).catch(() => {});
    }));
  }
}

function setupLoadingScreen() {
  const screen = document.getElementById("app-loading-screen");
  if (!screen) return;

  const title = document.getElementById("app-loading-title");
  const message = document.getElementById("app-loading-message");
  const forms = document.querySelectorAll("form[data-loading-title]");

  const reset = () => {
    screen.classList.add("hidden");
    screen.setAttribute("aria-hidden", "true");
    document.body.classList.remove("is-processing");
    document.body.removeAttribute("aria-busy");
    forms.forEach((form) => delete form.dataset.submitting);
  };

  forms.forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (event.defaultPrevented) return;
      if (form.dataset.submitting === "true") {
        event.preventDefault();
        return;
      }

      event.preventDefault();
      form.dataset.submitting = "true";
      const submitter = event.submitter;
      if (submitter?.hasAttribute("formaction")) form.action = submitter.formAction;
      if (submitter?.hasAttribute("formmethod")) form.method = submitter.formMethod;
      if (title) title.textContent = form.dataset.loadingTitle;
      if (message) message.textContent = form.dataset.loadingMessage;
      screen.classList.remove("hidden");
      screen.setAttribute("aria-hidden", "false");
      document.body.classList.add("is-processing");
      document.body.setAttribute("aria-busy", "true");
      form.querySelectorAll('button[type="submit"], input[type="submit"]').forEach((button) => {
        button.disabled = true;
      });

      window.setTimeout(() => HTMLFormElement.prototype.submit.call(form), 80);
    });
  });

  window.addEventListener("pageshow", reset);
}

function setupMobileNavigation() {
  const button = document.getElementById("mobile-menu-btn");
  const sidebar = document.querySelector(".app-sidebar");
  if (!button || !sidebar) return;

  const close = () => {
    sidebar.classList.remove("mobile-open");
    button.setAttribute("aria-expanded", "false");
  };

  button.addEventListener("click", () => {
    const isOpen = sidebar.classList.toggle("mobile-open");
    button.setAttribute("aria-expanded", String(isOpen));
  });

  sidebar.querySelectorAll("a").forEach((link) => link.addEventListener("click", close));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") close();
  });
}

// Auto-dismiss flash messages after a delay (errors stay a bit longer).
function setupFlashAutoDismiss() {
  document.querySelectorAll(".flash-message").forEach((el) => {
    const delay = el.classList.contains("flash-error") ? 6000 : 4000;

    setTimeout(() => {
      // Skip if the user already closed it with the ✕ button
      if (!el.isConnected) return;

      el.style.transition = "opacity .5s";
      el.style.opacity = "0";

      setTimeout(() => {
        const container = el.parentElement;
        el.remove();
        if (container && container.classList.contains("flash-container") && !container.children.length) {
          container.remove();
        }
      }, 500);
    }, delay);
  });
}

// Accordion toggle helper for Cleaning Operations
function toggleAccordion(id) {
  const content = document.getElementById("content-" + id);
  const icon = document.getElementById("icon-" + id);
  const item = document.getElementById("item-" + id);

  if (!content) return;

  const isHidden = content.classList.contains("hidden");
  if (isHidden) {
    content.classList.remove("hidden");
    if (icon) icon.classList.add("open");
    if (item) item.classList.add("active");
  } else {
    content.classList.add("hidden");
    if (icon) icon.classList.remove("open");
    if (item) item.classList.remove("active");
  }
}

// Hide the dropzone and reveal the file-state-card when a file is chosen.
// The browse button and dropzone are disabled so clicking cannot trigger a picker.
function setupUploadFeedback() {
  const input = document.getElementById("file-input");
  const dropzone = document.getElementById("dropzone");
  const browseBtn = document.getElementById("browse-btn");
  const fileStateCard = document.getElementById("file-state-card");
  const fileNameDisplay = document.getElementById("file-name");
  const fileMetaDisplay = document.getElementById("file-meta");
  const form = document.getElementById("upload-form");
  const uploadButton = document.getElementById("upload-btn");
  const changeFileBtn = document.getElementById("change-file-btn");

  if (!input) return;

  const handleFile = (file) => {
    if (!file) return;
    const sizeMb = (file.size / (1024 * 1024)).toFixed(2);

    if (fileNameDisplay) fileNameDisplay.textContent = file.name;
    if (fileMetaDisplay) fileMetaDisplay.textContent = sizeMb + " MB • Ready for Ingestion";

    // Completely lock and hide dropzone and browse option
    if (dropzone) {
      dropzone.style.display = "none";
      dropzone.classList.add("hidden");
    }
    if (browseBtn) {
      browseBtn.disabled = true;
      browseBtn.style.pointerEvents = "none";
    }
    if (fileStateCard) {
      fileStateCard.classList.remove("hidden");
    }
    if (uploadButton) {
      uploadButton.disabled = false;
    }
  };

  // Wire browse button to trigger file input
  if (browseBtn) {
    browseBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      input.click();
    });
  }

  // Clicking anywhere in the dropzone (when active) triggers file input
  if (dropzone) {
    dropzone.addEventListener("click", () => {
      if (dropzone.style.display !== "none" && !dropzone.classList.contains("hidden")) {
        input.click();
      }
    });

    ["dragenter", "dragover"].forEach((event) => {
      dropzone.addEventListener(event, (e) => {
        e.preventDefault();
        dropzone.classList.add("dragover");
      });
    });

    ["dragleave", "drop"].forEach((event) => {
      dropzone.addEventListener(event, (e) => {
        e.preventDefault();
        dropzone.classList.remove("dragover");
        if (event === "drop" && e.dataTransfer && e.dataTransfer.files.length > 0) {
          input.files = e.dataTransfer.files;
          handleFile(e.dataTransfer.files[0]);
        }
      });
    });
  }

  input.addEventListener("change", () => {
    if (input.files.length > 0) handleFile(input.files[0]);
  });

  // When form submits, freeze all buttons to prevent double-clicks
  if (form) {
    form.addEventListener("submit", () => {
      if (uploadButton) {
        uploadButton.disabled = true;
        uploadButton.innerHTML = '<span class="material-symbols-outlined text-[18px] animate-spin">sync</span><span>Uploading &amp; Analyzing Dataset...</span>';
      }
      if (changeFileBtn) {
        changeFileBtn.disabled = true;
        changeFileBtn.style.pointerEvents = "none";
      }
      if (browseBtn) {
        browseBtn.disabled = true;
      }
    });
  }
}

// Restore the dropzone and open the file picker (called by "Change" button).
function resetDropzone() {
  const dropzone = document.getElementById("dropzone");
  const browseBtn = document.getElementById("browse-btn");
  const fileStateCard = document.getElementById("file-state-card");
  const input = document.getElementById("file-input");

  if (dropzone) {
    dropzone.style.display = "";
    dropzone.classList.remove("hidden");
  }
  if (browseBtn) {
    browseBtn.disabled = false;
    browseBtn.style.pointerEvents = "auto";
  }
  if (fileStateCard) {
    fileStateCard.classList.add("hidden");
  }
  if (input) {
    input.value = "";
    setTimeout(() => input.click(), 50);
  }
}

// Show the custom-value input only when "custom" is selected for a column.
function setupCustomValueToggle() {
  document.querySelectorAll("select[data-toggle]").forEach((select) => {
    const target = document.getElementById(select.dataset.toggle);
    if (!target) return;

    const updateVisibility = () => {
      target.classList.toggle("hidden", select.value !== "custom");
      if (select.value === "custom") {
        const input = target.querySelector("input");
        if (input) input.focus();
      }
    };

    updateVisibility();
    select.addEventListener("change", updateVisibility);
  });
}

// Interactive real-time search filter on preview table rows.
function setupTableSearch() {
  const searchInput = document.getElementById("preview-search");
  const table = document.getElementById("preview-data-table");
  if (!searchInput || !table) return;

  const rows = table.querySelectorAll("tbody tr");

  searchInput.addEventListener("input", (e) => {
    const query = e.target.value.toLowerCase().trim();

    rows.forEach((row) => {
      const text = Array.from(row.querySelectorAll("td:not(.col-index)"))
        .map((td) => td.textContent.toLowerCase())
        .join(" ");

      const matches = text.includes(query);
      row.style.display = matches ? "" : "none";
    });
  });
}

function setupCategoryMapping() {
  const builders = Array.from(document.querySelectorAll(".category-map-builder"));
  if (!builders.length) return;

  builders.forEach((builder) => {
    const index = builder.dataset.mapIndex;
    const target = builder.querySelector(".category-map-target");
    const addBtn = builder.querySelector(".add-category-map");
    const rules = builder.querySelector(".category-map-rules");
    if (!index || !target || !addBtn || !rules) return;

    let ruleCounter = 0;
    const refreshEmptyState = () => {
      const hasRules = Boolean(rules.querySelector(".category-map-rule"));
      rules.querySelector(".category-map-empty")?.toggleAttribute("hidden", hasRules);
    };
    const notifyChange = () => rules.dispatchEvent(new Event("change", { bubbles: true }));
    const addHidden = (rule, name, value) => {
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = name;
      input.value = value;
      rule.appendChild(input);
    };

    const commitRule = () => {
      const selected = Array.from(
        builder.querySelectorAll(".category-map-source-check:checked:not(:disabled)")
      );
      const standard = target.value.trim();
      const changedSources = selected.filter((checkbox) => checkbox.value.trim() !== standard);
      target.setCustomValidity(
        !selected.length
          ? "Select at least one source value."
          : !standard
            ? "Choose or type the standard label."
            : !changedSources.length
              ? "Select at least one value that differs from the standard label."
              : ""
      );
      if (!target.reportValidity()) return false;

      ruleCounter += 1;
      const rule = document.createElement("div");
      rule.className = "category-map-rule";
      const summary = document.createElement("span");
      summary.className = "category-map-rule-summary font-mono text-xs";
      summary.textContent = `${selected.map((checkbox) => checkbox.value).join(", ")} -> ${standard}`;
      rule.appendChild(summary);

      changedSources.forEach((checkbox, sourceIndex) => {
        const suffix = `${ruleCounter}_${sourceIndex + 1}`;
        addHidden(rule, `mapvar_${index}_${suffix}`, checkbox.value);
        addHidden(rule, `map_${index}_${suffix}`, standard);
      });

      const removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.className = "category-map-rule-remove";
      removeBtn.setAttribute("aria-label", `Remove mapping to ${standard}`);
      removeBtn.textContent = "\u00d7";
      removeBtn.addEventListener("click", () => {
        selected.forEach((checkbox) => {
          checkbox.disabled = false;
          checkbox.closest(".category-map-option")?.removeAttribute("aria-disabled");
        });
        rule.remove();
        refreshEmptyState();
        notifyChange();
      });
      rule.appendChild(removeBtn);
      rules.appendChild(rule);

      selected.forEach((checkbox) => {
        checkbox.checked = false;
        checkbox.disabled = true;
        checkbox.closest(".category-map-option")?.setAttribute("aria-disabled", "true");
      });
      target.value = "";
      refreshEmptyState();
      notifyChange();
      return true;
    };

    addBtn.addEventListener("click", () => {
      if (commitRule()) target.focus();
    });
    target.addEventListener("input", () => target.setCustomValidity(""));
    builder.querySelectorAll(".category-map-source-check").forEach((checkbox) => {
      checkbox.addEventListener("change", () => target.setCustomValidity(""));
    });
    refreshEmptyState();
  });
}

// Compact multi-column conversion editor
function setupMultiColumnConversion() {
  const addBtn = document.getElementById("add-conversion-btn");
  const builder = document.getElementById("conversion-builder");
  const list = document.getElementById("conversion-list");
  const clearBtn = document.getElementById("clear-conversions-btn");
  if (!addBtn || !builder || !list) return;

  const column = document.getElementById("conversion-column");
  const type = document.getElementById("conversion-type");
  const format = document.getElementById("conversion-format");
  const policy = document.getElementById("conversion-policy");
  if (!column || !type || !format || !policy) return;

  const notifyChange = () => list.dispatchEvent(new Event("change", { bubbles: true }));
  const refreshEmptyState = () => {
    const hasItems = Boolean(list.querySelector(".conversion-item"));
    list.querySelector(".conversion-empty")?.toggleAttribute("hidden", hasItems);
    if (clearBtn) clearBtn.hidden = !hasItems;
  };

  const addHidden = (item, name, value) => {
    const input = document.createElement("input");
    input.type = "hidden";
    input.name = name;
    input.value = value;
    item.appendChild(input);
  };

  addBtn.addEventListener("click", () => {
    column.setCustomValidity(column.value ? "" : "Choose a column to convert.");
    type.setCustomValidity(type.value ? "" : "Choose the target datatype.");
    if (!column.reportValidity() || !type.reportValidity()) return;

    Array.from(list.querySelectorAll(".conversion-item"))
      .find((existing) => existing.dataset.column === column.value)
      ?.remove();

    const item = document.createElement("div");
    item.className = "conversion-item";
    item.dataset.column = column.value;

    const summary = document.createElement("span");
    summary.className = "conversion-item-summary font-mono text-xs";
    const formatLabel = format.value ? ` / ${format.options[format.selectedIndex].text}` : "";
    const policyLabel = policy.value === "coerce_to_missing" ? " / invalid to missing" : "";
    summary.textContent = `${column.value} -> ${type.options[type.selectedIndex].text}${formatLabel}${policyLabel}`;
    item.appendChild(summary);

    addHidden(item, "convert_column", column.value);
    addHidden(item, "convert_type", type.value);
    addHidden(item, "convert_format", format.value);
    addHidden(item, "convert_policy", policy.value);

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "conversion-remove";
    removeBtn.setAttribute("aria-label", `Remove conversion for ${column.value}`);
    removeBtn.textContent = "\u00d7";
    removeBtn.addEventListener("click", () => {
      item.remove();
      refreshEmptyState();
      notifyChange();
    });
    item.appendChild(removeBtn);
    list.appendChild(item);

    column.value = "";
    type.value = "";
    format.value = "";
    policy.value = "reject";
    refreshEmptyState();
    notifyChange();
    column.focus();
  });

  [column, type].forEach((select) => {
    select.addEventListener("change", () => select.setCustomValidity(""));
  });

  clearBtn?.addEventListener("click", () => {
    list.querySelectorAll(".conversion-item").forEach((item) => item.remove());
    refreshEmptyState();
    notifyChange();
  });

  refreshEmptyState();
}

function setupValidationRules() {
  const form = document.getElementById("cleaning-form");
  const column = document.getElementById("validation-column");
  const rule = document.getElementById("validation-rule");
  const action = document.getElementById("validation-action");
  const tokens = document.getElementById("validation-tokens");
  const addButton = document.getElementById("add-validation-rule");
  const list = document.getElementById("validation-rule-list");
  if (!form || !column || !rule || !action || !tokens || !addButton || !list) return;

  const field = (id) => document.getElementById(id);
  const parameters = Array.from(document.querySelectorAll(".validation-param"));
  const setError = (control, message) => {
    control.setCustomValidity(message);
    if (message) control.reportValidity();
    return !message;
  };
  const refresh = () => {
    parameters.forEach((control) => {
      const applies = (control.dataset.validationRules || "").split(" ").includes(rule.value);
      control.hidden = !applies;
      if (!applies) control.setCustomValidity("");
    });
    const setMissing = action.querySelector('option[value="set_missing"]');
    if (setMissing) setMissing.disabled = rule.value === "required";
    if (rule.value === "required" && action.value === "set_missing") action.value = "report";
  };
  const refreshEmpty = () => {
    list.querySelector(".validation-empty")?.toggleAttribute(
      "hidden", Boolean(list.querySelector(".validation-rule-item"))
    );
  };
  const notifyChange = () => list.dispatchEvent(new Event("change", { bubbles: true }));

  const addRule = () => {
    parameters.forEach((control) => control.setCustomValidity(""));
    if (!setError(column, column.value ? "" : "Choose a column to validate.")) return false;
    if (!setError(rule, rule.value ? "" : "Choose a validation rule.")) return false;

    const operation = {
      type: "validate",
      column: column.value,
      rule: rule.value,
      action: action.value || "report",
      tokens: tokens.checked ? ["n/a", "na", "null", "-", "--"] : [],
    };
    let detail = "";
    if (rule.value === "range") {
      const minimum = field("validation-min");
      const maximum = field("validation-max");
      if (!minimum.value && !maximum.value) {
        setError(minimum, "Enter a minimum, maximum, or both.");
        return false;
      }
      if (minimum.value) operation.minimum = minimum.value;
      if (maximum.value) operation.maximum = maximum.value;
      if (minimum.value && maximum.value && Number(minimum.value) > Number(maximum.value)) {
        setError(minimum, "Minimum cannot exceed maximum.");
        return false;
      }
      detail = `${minimum.value || "-inf"} to ${maximum.value || "inf"}`;
    } else if (rule.value === "allowed") {
      const values = field("validation-values");
      operation.values = values.value.split(",").map((value) => value.trim()).filter(Boolean);
      if (!setError(values, operation.values.length ? "" : "Enter at least one allowed value.")) return false;
      detail = operation.values.join(", ");
    } else if (rule.value === "pattern") {
      const pattern = field("validation-pattern");
      if (!setError(pattern, pattern.value ? "" : "Enter a pattern.")) return false;
      operation.pattern = pattern.value;
      detail = pattern.value;
    } else if (rule.value === "outlier") {
      const multiplier = field("validation-multiplier");
      operation.multiplier = multiplier.value || "1.5";
      const value = Number(operation.multiplier);
      if (!setError(multiplier, value >= 0.5 && value <= 10 ? "" : "Use a multiplier from 0.5 to 10.")) return false;
      detail = `${operation.multiplier}x IQR`;
    }

    const item = document.createElement("div");
    item.className = "validation-rule-item";
    const summary = document.createElement("span");
    summary.className = "font-mono text-xs";
    const actionLabel = operation.action === "report" ? "report" : operation.action.replace("_", " ");
    summary.textContent = `${operation.column} / ${operation.rule}${detail ? `: ${detail}` : ""} / ${actionLabel}`;
    item.appendChild(summary);
    const hidden = document.createElement("input");
    hidden.type = "hidden";
    hidden.name = "validation_rule_json";
    hidden.value = JSON.stringify(operation);
    item.appendChild(hidden);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "conversion-remove";
    remove.setAttribute("aria-label", `Remove validation rule for ${operation.column}`);
    remove.textContent = "\u00d7";
    remove.addEventListener("click", () => {
      item.remove();
      refreshEmpty();
      notifyChange();
    });
    item.appendChild(remove);
    list.appendChild(item);

    column.value = "";
    rule.value = "";
    action.value = "report";
    tokens.checked = false;
    parameters.forEach((control) => { control.value = ""; });
    refresh();
    refreshEmpty();
    notifyChange();
    return true;
  };

  addButton.addEventListener("click", addRule);
  rule.addEventListener("change", refresh);
  [column, rule].forEach((control) => control.addEventListener("change", () => control.setCustomValidity("")));
  refresh();
  refreshEmpty();
}

// Track active operations and display on submit button
function setupActiveOperationsCount() {
  const form = document.getElementById("cleaning-form");
  const submitBtn = document.getElementById("apply-cleaning-btn");
  const countDisplay = document.getElementById("active-ops-count");
  if (!form || !submitBtn) return;

  const countActive = () => {
    let count = 0;

    form.querySelectorAll('input[type="checkbox"][name]:checked').forEach(() => count++);

    form.querySelectorAll('select[name]:not([name^="convert_"])').forEach((sel) => {
      if (sel.value && sel.value !== "") count++;
    });

    const conversionItems = form.querySelectorAll(".conversion-item").length;
    count += conversionItems;

    count += form.querySelectorAll(".category-map-rule").length;
    count += form.querySelectorAll(".validation-rule-item").length;

    if (countDisplay) {
      countDisplay.textContent = count + " active operation(s) configured";
    }
  };

  form.addEventListener("change", countActive);
  form.addEventListener("input", countActive);
  countActive();
}

// Ask for confirmation when the user chooses destructive actions (row/col removal).
function setupDestructiveConfirm() {
  const form = document.getElementById("cleaning-form");
  if (!form) return;

  form.addEventListener("submit", (e) => {
    const removesRows = Array.from(form.querySelectorAll("select[name]"))
      .some((select) => select.value === "remove")
      || Array.from(form.querySelectorAll('input[name="validation_rule_json"]'))
        .some((input) => {
          try { return JSON.parse(input.value).action === "remove"; } catch (error) { return false; }
        });

    const dropsCols = Array.from(form.querySelectorAll('input[name="drop_columns"]:checked')).length > 0;

    if (removesRows && dropsCols) {
      if (!confirm("Some selected operations will drop columns and delete rows. Proceed with cleaning?")) {
        e.preventDefault();
      }
    } else if (removesRows) {
      if (!confirm("Some selected operations will remove rows from your dataset. Proceed?")) {
        e.preventDefault();
      }
    } else if (dropsCols) {
      if (!confirm("You have selected columns to be dropped permanently. Proceed?")) {
        e.preventDefault();
      }
    }
  });
}

// Global Theme Management (Light / Dark)
function setupThemeToggle() {
  const current = document.documentElement.getAttribute("data-theme") || "light";
  updateThemeToggleUI(current);
}

function updateThemeToggleUI(theme) {
  const button = document.getElementById("theme-toggle-btn");
  const icon = document.getElementById("theme-icon");
  const label = document.getElementById("theme-label");
  if (!icon || !label) return;
  if (theme === "dark") {
    icon.textContent = "light_mode";
    label.textContent = "Light Mode";
    if (button) button.setAttribute("aria-label", "Switch to light mode");
  } else {
    icon.textContent = "dark_mode";
    label.textContent = "Dark Mode";
    if (button) button.setAttribute("aria-label", "Switch to dark mode");
  }
}

function toggleTheme() {
  const current = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
  const next = current === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  try {
    localStorage.setItem("dataclean-theme", next);
  } catch (e) {}
  updateThemeToggleUI(next);
}
