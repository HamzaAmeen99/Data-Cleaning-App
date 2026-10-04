// DataClean OS — Client-side interactions and UI helpers

document.addEventListener("DOMContentLoaded", () => {
  setupUploadFeedback();
  setupCustomValueToggle();
  setupTableSearch();
  setupMultiColumnConversion();
  setupDestructiveConfirm();
  setupActiveOperationsCount();
});

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

// Show the chosen filename on the upload box and enable the submit button.
function setupUploadFeedback() {
  const input = document.getElementById("file-input");
  const uploadText = document.getElementById("upload-text");
  const uploadButton = document.getElementById("upload-btn");
  const fileStateCard = document.getElementById("file-state-card");
  const fileNameDisplay = document.getElementById("file-name");
  const fileMetaDisplay = document.getElementById("file-meta");
  const form = document.getElementById("upload-form");
  const dropzone = document.getElementById("dropzone");

  if (!input) return;

  const handleFile = (file) => {
    if (!file) return;
    const sizeMb = (file.size / (1024 * 1024)).toFixed(2);

    if (uploadText) {
      uploadText.textContent = `${file.name} (${sizeMb} MB)`;
    }

    if (fileNameDisplay) {
      fileNameDisplay.textContent = file.name;
    }

    if (fileMetaDisplay) {
      fileMetaDisplay.textContent = `${sizeMb} MB • Ready for Ingestion`;
    }

    if (fileStateCard) {
      fileStateCard.classList.remove("hidden");
    }

    if (uploadButton) {
      uploadButton.disabled = false;
    }
  };

  input.addEventListener("change", () => {
    if (input.files.length > 0) {
      handleFile(input.files[0]);
    }
  });

  if (dropzone) {
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
        if (event === "drop" && e.dataTransfer.files.length > 0) {
          input.files = e.dataTransfer.files;
          handleFile(input.files[0]);
        }
      });
    });
  }

  if (form) {
    form.addEventListener("submit", () => {
      if (uploadButton) {
        uploadButton.disabled = true;
        uploadButton.innerHTML = `<span class="material-symbols-outlined text-[18px]">sync</span> Uploading & Analyzing…`;
      }
    });
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

// Dynamic multi-column conversion rows
function setupMultiColumnConversion() {
  const addBtn = document.getElementById("add-conversion-btn");
  const container = document.getElementById("conversion-container");
  if (!addBtn || !container) return;

  addBtn.addEventListener("click", () => {
    const firstRow = container.querySelector(".conversion-row");
    if (!firstRow) return;

    const newRow = firstRow.cloneNode(true);
    newRow.querySelectorAll("select").forEach((sel) => {
      sel.selectedIndex = 0;
    });

    if (!newRow.querySelector(".remove-row-btn")) {
      const removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.className = "btn btn-sm btn-outline remove-row-btn";
      removeBtn.innerHTML = "✕";
      removeBtn.title = "Remove this conversion";
      removeBtn.addEventListener("click", () => newRow.remove());
      newRow.appendChild(removeBtn);
    }

    container.appendChild(newRow);
  });
}

// Track active operations and display on submit button
function setupActiveOperationsCount() {
  const form = document.getElementById("cleaning-form");
  const submitBtn = document.getElementById("apply-cleaning-btn");
  const countDisplay = document.getElementById("active-ops-count");
  if (!form || !submitBtn) return;

  const countActive = () => {
    let count = 0;

    form.querySelectorAll('input[type="checkbox"]:checked').forEach(() => count++);

    form.querySelectorAll("select").forEach((sel) => {
      if (sel.value && sel.value !== "") count++;
    });

    form.querySelectorAll(".map-input").forEach((inp) => {
      if (inp.value && inp.value.trim() !== "") count++;
    });

    if (countDisplay) {
      countDisplay.textContent = `${count} active operation(s) configured`;
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
    const removesRows = Array.from(form.querySelectorAll("select"))
      .some((select) => select.value === "remove");

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
