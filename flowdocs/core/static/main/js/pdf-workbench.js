(() => {
  "use strict";

  const parseJson = (value) => {
    if (!value) return {};
    try {
      return JSON.parse(value);
    } catch (_error) {
      return {};
    }
  };

  const csrfToken = () => {
    const field = document.querySelector('input[name="csrfmiddlewaretoken"]');
    if (field?.value) return field.value;
    const cookie = document.cookie
      .split(";")
      .map((part) => part.trim())
      .find((part) => part.startsWith("csrftoken="));
    return cookie ? decodeURIComponent(cookie.slice("csrftoken=".length)) : "";
  };

  const errorMessage = (payload, fallback) =>
    payload?.error?.message ||
    payload?.detail ||
    payload?.message ||
    (typeof payload?.error === "string" ? payload.error : "") ||
    fallback;

  const setupActionModal = () => {
    const modalElement = document.getElementById("actionModal");
    if (!modalElement || !window.bootstrap?.Modal) return;

    const modal = window.bootstrap.Modal.getOrCreateInstance(modalElement);
    const form = document.getElementById("actionModalForm");
    const title = document.getElementById("actionModalTitle");
    const documentName = document.getElementById("actionModalDocument");
    const type = document.getElementById("actionModalType");
    const submit = document.getElementById("actionModalSubmit");
    const renameFields = document.getElementById("actionRenameFields");
    const ownerFields = document.getElementById("actionOwnerFields");
    const newTitle = document.getElementById("actionNewTitle");
    const owner = document.getElementById("actionOwner");

    const setActiveFields = (action) => {
      [renameFields, ownerFields].forEach((group) => {
        const active = group?.dataset.actionFields === action;
        if (!group) return;
        group.hidden = !active;
        group.querySelectorAll("input, select, textarea").forEach((control) => {
          control.disabled = !active;
        });
      });
    };

    document.querySelectorAll("[data-action][data-action-url]").forEach((button) => {
      button.addEventListener("click", () => {
        const action = button.dataset.action;
        const pdfTitle = button.dataset.pdfTitle || "";
        if (!form || !title || !type || !submit) return;
        if (action !== "rename" && action !== "assign-owner") return;

        setActiveFields(action);
        form.action = button.dataset.actionUrl;
        type.value = action;
        documentName.textContent = pdfTitle;

        if (action === "rename") {
          title.textContent = modalElement.dataset.renameTitle;
          submit.textContent = modalElement.dataset.renameSubmit;
          newTitle.value = pdfTitle;
        } else {
          title.textContent = modalElement.dataset.ownerTitle;
          submit.textContent = modalElement.dataset.ownerSubmit;
          owner.value = "";
        }

        modalElement.addEventListener(
          "shown.bs.modal",
          () => (action === "rename" ? newTitle : owner)?.focus(),
          { once: true }
        );
        modal.show();
      });
    });
  };

  const setupConfirmModal = () => {
    const modalElement = document.getElementById("confirmActionModal");
    if (!modalElement || !window.bootstrap?.Modal) return;

    const modal = window.bootstrap.Modal.getOrCreateInstance(modalElement);
    const title = document.getElementById("confirmActionTitle");
    const message = document.getElementById("confirmActionMessage");
    const context = document.getElementById("confirmActionContext");
    const submit = document.getElementById("confirmActionSubmit");
    let pendingForm = null;

    document.querySelectorAll("form[data-confirm-form]").forEach((form) => {
      form.addEventListener("submit", (event) => {
        if (form.dataset.confirmed === "true") {
          delete form.dataset.confirmed;
          return;
        }
        event.preventDefault();
        pendingForm = form;
        const recordTitle = form
          .closest(".document-record")
          ?.querySelector(".document-record__identity strong")?.textContent;

        title.textContent = form.dataset.confirmTitle || title.textContent;
        message.textContent = form.dataset.confirmMessage || "";
        context.textContent = recordTitle?.trim() || "";
        submit.textContent = form.dataset.confirmSubmit || submit.textContent;
        submit.classList.toggle("btn-danger", form.dataset.confirmDanger === "true");
        submit.classList.toggle("btn-primary", form.dataset.confirmDanger !== "true");
        modal.show();
      });
    });

    submit?.addEventListener("click", () => {
      if (!pendingForm) return;
      const form = pendingForm;
      pendingForm = null;
      form.dataset.confirmed = "true";
      modal.hide();
      form.requestSubmit();
    });

    modalElement.addEventListener("hidden.bs.modal", () => {
      pendingForm = null;
    });
  };

  const setupIntake = () => {
    const root = document.querySelector("[data-pdf-intake]");
    if (!root) return;

    const input = root.querySelector("[data-intake-input]");
    const dropzone = root.querySelector("[data-intake-dropzone]");
    const manifest = root.querySelector("[data-intake-manifest]");
    const list = root.querySelector("[data-intake-list]");
    const template = document.getElementById("pdf-intake-item-template");
    const count = root.querySelector("[data-intake-count]");
    const summary = root.querySelector("[data-intake-summary]");
    const live = root.querySelector("[data-intake-live]");
    const retryAll = root.querySelector("[data-intake-retry-all]");
    const receive = root.querySelector("[data-intake-receive]");
    const finalize = root.querySelector("[data-intake-finalize]");
    const discard = root.querySelector("[data-intake-discard]");
    const newBatch = root.querySelector("[data-intake-new]");
    if (!input || !dropzone || !manifest || !list || !template) return;

    const maxFiles = Number.parseInt(root.dataset.maxFiles || "50", 10);
    const maxFileSize = Number.parseInt(root.dataset.maxFileSizeBytes || "0", 10);
    const concurrency = Number.parseInt(root.dataset.concurrency || "3", 10);
    const placeholder = root.dataset.batchPlaceholder || "";
    const storageKey = `pdf-intake:${root.dataset.folderId}`;
    const items = new Map();
    const queue = [];
    let batch = null;
    let endpoints = {};
    let createRequest = null;
    let activeUploads = 0;
    let pollTimer = null;
    let finalized = false;
    let sequence = 0;

    const announce = (message) => {
      if (live) live.textContent = message || "";
    };

    const batchId = (value) => value?.id || value?.uuid || value?.public_id || value?.batch_id;

    const endpointFromTemplate = (name, id) => {
      const source = root.dataset[`${name}UrlTemplate`] || "";
      return source && placeholder ? source.replace(placeholder, encodeURIComponent(id)) : source;
    };

    const normalizeEndpoints = (payload, id) => {
      const data = payload?.data || payload || {};
      const supplied = data.endpoints || data.urls || data.batch?.endpoints || {};
      return {
        detail:
          supplied.detail ||
          supplied.status ||
          data.detail_url ||
          data.status_url ||
          endpointFromTemplate("detail", id),
        item:
          supplied.item ||
          supplied.items ||
          data.item_url ||
          endpointFromTemplate("item", id),
        finalize:
          supplied.finalize || data.finalize_url || endpointFromTemplate("finalize", id),
        discard:
          supplied.discard || data.discard_url || endpointFromTemplate("discard", id),
      };
    };

    const persistBatch = () => {
      const id = batchId(batch);
      if (!id) return;
      try {
        window.localStorage.setItem(storageKey, JSON.stringify({ id, endpoints }));
      } catch (_error) {
        // The URL still carries the resumable batch reference.
      }
      const url = new URL(window.location.href);
      url.searchParams.set("intake_batch", id);
      window.history.replaceState({}, "", url);
    };

    const clearPersistedBatch = () => {
      try {
        window.localStorage.removeItem(storageKey);
      } catch (_error) {
        // Storage may be unavailable in hardened browser modes.
      }
      const url = new URL(window.location.href);
      url.searchParams.delete("intake_batch");
      window.history.replaceState({}, "", url);
    };

    const uniqueKey = () => {
      if (window.crypto?.randomUUID) return window.crypto.randomUUID();
      return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    };

    const fileFingerprint = (file) => `${file.name}:${file.size}:${file.lastModified}`;

    const titleFromFilename = (filename) =>
      filename
        .replace(/\.pdf$/i, "")
        .replace(/[_-]+/g, " ")
        .replace(/\s+/g, " ")
        .trim();

    const formatBytes = (bytes) => {
      if (!Number.isFinite(bytes)) return "";
      if (bytes < 1024) return `${bytes} B`;
      if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
      return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    };

    const stateLabel = (state) => {
      if (state === "uploading") return root.dataset.textUploading;
      if (state === "received") return root.dataset.textReceived;
      if (state === "processing_queued") return root.dataset.textQueued;
      if (state === "processing") return root.dataset.textProcessing;
      if (state === "searchable") return root.dataset.textSearchable;
      if (state === "error" || state === "needs_attention") return root.dataset.textFailed;
      if (state === "queued") return root.dataset.textSelected;
      return root.dataset.textWaiting;
    };

    const renderItem = (item) => {
      const node = item.node;
      if (!node) return;
      const state = node.querySelector("[data-intake-state]");
      const progress = node.querySelector("[data-intake-progress]");
      const error = node.querySelector("[data-intake-error]");
      const retry = node.querySelector("[data-intake-retry]");
      const remove = node.querySelector("[data-intake-remove]");
      const title = node.querySelector("[data-intake-title]");

      node.dataset.state = item.state;
      state.textContent = stateLabel(item.state);
      progress.value = item.progress || 0;
      const progressFallback = progress.querySelector("span");
      if (progressFallback) progressFallback.textContent = `${item.progress || 0}%`;
      error.textContent = item.error || "";
      retry.hidden =
        finalized ||
        (item.state !== "error" && item.state !== "needs_attention") ||
        item.retryable === false;
      remove.disabled = item.state === "uploading" || finalized;
      title.disabled = item.state === "uploading" || item.state === "received" || finalized;
      title.setAttribute("aria-invalid", item.state === "error" && !item.title ? "true" : "false");
    };

    const updateSummary = () => {
      const values = [...items.values()];
      const received = values.filter((item) =>
        ["received", "processing_queued", "processing", "searchable"].includes(item.state)
      ).length;
      const failed = values.filter((item) =>
        ["error", "needs_attention"].includes(item.state)
      ).length;
      const uploading = values.filter((item) => item.state === "uploading").length;
      const pending = values.length - received - failed - uploading;
      count.textContent = String(values.length);
      manifest.hidden = values.length === 0;
      summary.textContent = [
        `${received} ${root.dataset.textReceived}`,
        `${uploading} ${root.dataset.textUploading}`,
        `${pending} ${root.dataset.textWaiting}`,
        `${failed} ${root.dataset.textFailed}`,
      ].join(" · ");
      retryAll.hidden = finalized || failed === 0;
      if (newBatch) newBatch.hidden = !finalized;
      discard.disabled = values.length === 0 || activeUploads > 0 || finalized;
      if (receive) {
        receive.disabled =
          finalized ||
          activeUploads > 0 ||
          !values.some((item) => item.state === "queued" && item.file && item.title);
      }
      finalize.disabled =
        finalized ||
        !batch ||
        values.length === 0 ||
        activeUploads > 0 ||
        values.some((item) => item.state !== "received");
      root.setAttribute("aria-busy", activeUploads > 0 ? "true" : "false");
    };

    const removeLocalItem = (item) => {
      item.xhr?.abort();
      item.node?.remove();
      items.delete(item.localId);
      const queuedIndex = queue.indexOf(item.localId);
      if (queuedIndex >= 0) queue.splice(queuedIndex, 1);
      updateSummary();
    };

    const deleteServerReceipt = async (item) => {
      if (!item.serverId || !item.removeUrl) return true;
      const response = await window.fetch(item.removeUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          "X-CSRFToken": csrfToken(),
          "X-Requested-With": "XMLHttpRequest",
        },
      });
      return response.ok;
    };

    const removeItem = async (item) => {
      if (!item.serverId) {
        removeLocalItem(item);
        return;
      }
      try {
        if (!(await deleteServerReceipt(item))) throw new Error("remove_failed");
        removeLocalItem(item);
      } catch (_error) {
        item.state = "error";
        item.error = root.dataset.textUploadError;
        renderItem(item);
        updateSummary();
      }
    };

    const bindItemNode = (item) => {
      const fragment = template.content.cloneNode(true);
      const node = fragment.querySelector("[data-intake-item]");
      const title = node.querySelector("[data-intake-title]");
      const titleLabel = node.querySelector("[data-intake-title-label]");
      const retry = node.querySelector("[data-intake-retry]");
      const remove = node.querySelector("[data-intake-remove]");
      const titleId = `intake-title-${item.localId}`;

      item.node = node;
      node.dataset.itemId = item.localId;
      node.querySelector("[data-intake-sequence]").textContent = String(item.sequence);
      node.querySelector("[data-intake-filename]").textContent = item.filename;
      node.querySelector("[data-intake-size]").textContent = formatBytes(item.size);
      title.id = titleId;
      title.value = item.title;
      titleLabel.htmlFor = titleId;
      titleLabel.textContent = root.dataset.textTitleLabel;
      retry.setAttribute("aria-label", `${root.dataset.textRetryLabel}: ${item.filename}`);
      remove.setAttribute("aria-label", `${root.dataset.textRemoveLabel}: ${item.filename}`);

      title.addEventListener("input", () => {
        item.title = title.value.trim();
        if (item.state === "error" && item.file && item.title) {
          item.error = "";
        }
        renderItem(item);
        updateSummary();
      });
      retry.addEventListener("click", async () => {
        if (!item.file) {
          announce(root.dataset.textRetryReselect);
          input.click();
          return;
        }
        if (!(await deleteServerReceipt(item))) {
          item.error = root.dataset.textUploadError;
          renderItem(item);
          return;
        }
        item.serverId = null;
        item.removeUrl = "";
        item.idempotencyKey = uniqueKey();
        item.error = "";
        item.state = "queued";
        item.progress = 0;
        queue.push(item.localId);
        renderItem(item);
        updateSummary();
        void pumpQueue();
      });
      remove.addEventListener("click", () => void removeItem(item));
      list.appendChild(fragment);
      renderItem(item);
    };

    const newLocalItem = (file, options = {}) => {
      sequence += 1;
      const localId = options.localId || uniqueKey();
      const item = {
        localId,
        sequence,
        file: file || null,
        filename: options.filename || file?.name || "PDF",
        size: options.size ?? file?.size,
        fingerprint: file ? fileFingerprint(file) : options.fingerprint || "",
        title: options.title || titleFromFilename(options.filename || file?.name || "PDF"),
        idempotencyKey: options.idempotencyKey || uniqueKey(),
        serverId: options.serverId || null,
        removeUrl: options.removeUrl || "",
        state: options.state || "waiting",
        progress: options.progress || 0,
        error: options.error || "",
        retryable: options.retryable !== false,
        xhr: null,
        node: null,
      };
      items.set(localId, item);
      bindItemNode(item);
      return item;
    };

    const responseBatch = (payload) => {
      const data = payload?.data || payload || {};
      return data.batch || payload?.batch || data;
    };

    const ensureBatch = async () => {
      if (batch) return batch;
      if (createRequest) return createRequest;
      if (!root.dataset.createUrl) throw new Error("missing_create_url");

      createRequest = window
        .fetch(root.dataset.createUrl, {
          method: "POST",
          credentials: "same-origin",
          headers: {
            Accept: "application/json",
            "X-CSRFToken": csrfToken(),
            "X-Requested-With": "XMLHttpRequest",
          },
        })
        .then(async (response) => {
          const payload = parseJson(await response.text());
          if (!response.ok) throw new Error(errorMessage(payload, root.dataset.textCreateError));
          batch = responseBatch(payload);
          const id = batchId(batch);
          if (!id) throw new Error(root.dataset.textCreateError);
          endpoints = normalizeEndpoints(payload, id);
          persistBatch();
          updateSummary();
          startPolling();
          return batch;
        })
        .finally(() => {
          createRequest = null;
        });
      return createRequest;
    };

    const uploadItem = async (item) => {
      if (!item.file || item.state !== "queued") return;
      if (!item.title) {
        item.state = "error";
        item.error = root.dataset.textTitleLabel;
        renderItem(item);
        return;
      }

      activeUploads += 1;
      item.state = "uploading";
      item.progress = 0;
      renderItem(item);
      updateSummary();

      try {
        await ensureBatch();
      } catch (error) {
        activeUploads = Math.max(0, activeUploads - 1);
        item.state = "error";
        item.error = error.message || root.dataset.textCreateError;
        renderItem(item);
        updateSummary();
        return;
      }
      if (!endpoints.item) {
        activeUploads = Math.max(0, activeUploads - 1);
        item.state = "error";
        item.error = root.dataset.textCreateError;
        renderItem(item);
        updateSummary();
        return;
      }

      await new Promise((resolve) => {
        const request = new XMLHttpRequest();
        item.xhr = request;
        request.open("POST", endpoints.item);
        request.withCredentials = true;
        request.setRequestHeader("Accept", "application/json");
        request.setRequestHeader("X-CSRFToken", csrfToken());
        request.setRequestHeader("X-Requested-With", "XMLHttpRequest");
        request.upload.addEventListener("progress", (event) => {
          if (!event.lengthComputable) return;
          item.progress = Math.min(99, Math.round((event.loaded / event.total) * 100));
          renderItem(item);
        });
        request.addEventListener("load", () => {
          const payload = parseJson(request.responseText);
          const data = payload?.data || payload || {};
          const saved = data.item || payload?.item || data;
          item.serverId = saved.id || saved.uuid || saved.item_id || item.serverId;
          item.removeUrl = saved.remove_url || item.removeUrl;
          if (request.status >= 200 && request.status < 300) {
            item.state = "received";
            item.progress = 100;
            item.error = "";
            announce(`${item.filename}: ${root.dataset.textReceived}`);
          } else {
            item.state = "error";
            item.error =
              saved.error_message ||
              saved.error ||
              errorMessage(payload, root.dataset.textUploadError);
          }
          resolve();
        });
        request.addEventListener("error", () => {
          item.state = "error";
          item.error = root.dataset.textUploadError;
          resolve();
        });
        request.addEventListener("abort", () => resolve());

        const data = new FormData();
        data.append("file", item.file, item.file.name);
        data.append("title", item.title);
        data.append("idempotency_key", item.idempotencyKey);
        request.send(data);
      });

      item.xhr = null;
      activeUploads = Math.max(0, activeUploads - 1);
      renderItem(item);
      updateSummary();
    };

    async function pumpQueue() {
      while (activeUploads < concurrency && queue.length > 0) {
        const localId = queue.shift();
        const item = items.get(localId);
        if (!item || item.state !== "queued") continue;
        void uploadItem(item).finally(() => void pumpQueue());
      }
    }

    const pairRestoredItem = (file) =>
      [...items.values()].find(
        (item) =>
          !item.file &&
          item.state === "error" &&
          item.filename === file.name &&
          (!item.size || item.size === file.size)
      );

    const addFiles = (fileList) => {
      const selected = [...fileList];
      const available = Math.max(0, maxFiles - items.size);
      if (selected.length > available) announce(root.dataset.textLimit);

      selected.slice(0, available).forEach((file) => {
        const restored = pairRestoredItem(file);
        if (restored) {
          restored.file = file;
          restored.fingerprint = fileFingerprint(file);
          void deleteServerReceipt(restored).then((removed) => {
            if (!removed) {
              restored.error = root.dataset.textUploadError;
              renderItem(restored);
              return;
            }
            restored.serverId = null;
            restored.removeUrl = "";
            restored.idempotencyKey = uniqueKey();
            restored.state = "queued";
            restored.error = "";
            restored.progress = 0;
            queue.push(restored.localId);
            renderItem(restored);
            updateSummary();
            void pumpQueue();
          });
          return;
        }

        if ([...items.values()].some((item) => item.fingerprint === fileFingerprint(file))) {
          announce(root.dataset.textDuplicate);
          return;
        }
        const isPdf = file.name.toLowerCase().endsWith(".pdf") && (!file.type || file.type === "application/pdf");
        const withinSizeLimit = maxFileSize <= 0 || file.size <= maxFileSize;
        const item = newLocalItem(file, {
          state: isPdf && withinSizeLimit ? "queued" : "error",
          error: !isPdf
            ? root.dataset.textInvalidPdf
            : withinSizeLimit
              ? ""
              : root.dataset.textFileTooLarge,
          retryable: isPdf && withinSizeLimit,
        });
      });
      input.value = "";
      updateSummary();
    };

    const serverState = (value) => {
      const state = String(value || "").toLowerCase();
      if (state === "queued") return "processing_queued";
      if (state === "processing") return "processing";
      if (state === "searchable") return "searchable";
      if (state === "needs_attention") return "needs_attention";
      if (["accepted", "complete", "completed", "received", "stored", "uploaded", "valid"].includes(state)) return "received";
      if (["failed", "invalid", "rejected", "error"].includes(state)) return "error";
      if (["uploading", "running"].includes(state)) return "uploading";
      return "waiting";
    };

    const progressForState = (state, current = 0) => {
      if (state === "searchable" || state === "received") return 100;
      if (state === "processing") return Math.max(current, 75);
      if (state === "processing_queued") return Math.max(current, 55);
      return current;
    };

    const mergeServerItems = (serverItems) => {
      serverItems.forEach((serverItem) => {
        const id = serverItem.id || serverItem.uuid || serverItem.item_id;
        let item = [...items.values()].find(
          (candidate) =>
            (id && candidate.serverId === id) ||
            (serverItem.idempotency_key && candidate.idempotencyKey === serverItem.idempotency_key)
        );
        if (serverItem.state === "removed") {
          if (item) removeLocalItem(item);
          return;
        }
        const state = serverState(
          serverItem.display_state || serverItem.status || serverItem.state
        );
        if (!item) {
          item = newLocalItem(null, {
            filename: serverItem.filename || serverItem.original_filename || serverItem.file_name || "PDF",
            size: serverItem.size || serverItem.file_size,
            title: serverItem.title,
            idempotencyKey: serverItem.idempotency_key,
            serverId: id,
            removeUrl: serverItem.remove_url,
            state,
            progress: progressForState(state),
            error: serverItem.error_message || serverItem.error || "",
          });
        } else if (item.state !== "uploading") {
          item.serverId = id || item.serverId;
          item.removeUrl = serverItem.remove_url || item.removeUrl;
          item.state = state;
          item.progress = progressForState(state, item.progress);
          item.error = serverItem.error_message || serverItem.error || "";
          renderItem(item);
        }
      });
      updateSummary();
    };

    const refreshBatch = async () => {
      if (!batch || !endpoints.detail || document.hidden) return;
      try {
        const response = await window.fetch(endpoints.detail, {
          credentials: "same-origin",
          headers: { Accept: "application/json", "X-Requested-With": "XMLHttpRequest" },
        });
        const payload = parseJson(await response.text());
        if (!response.ok) {
          if (response.status === 404 || response.status === 410) {
            stopPolling();
            clearPersistedBatch();
            batch = null;
            endpoints = {};
            finalized = false;
            input.disabled = false;
            announce(root.dataset.textStatusError);
            updateSummary();
            return;
          }
          throw new Error("status_failed");
        }
        const data = payload?.data || payload || {};
        batch = data.batch || payload?.batch || batch;
        mergeServerItems(data.items || payload?.items || batch.items || []);
        const status = String(batch.status || "").toLowerCase();
        if (status === "finalized") {
          finalized = true;
          input.disabled = true;
          persistBatch();
          const jobStatus = String(batch.job?.status || "").toLowerCase();
          if (["completed", "failed", "cancelled"].includes(jobStatus)) {
            stopPolling();
            announce(
              jobStatus === "completed"
                ? root.dataset.textJobComplete
                : root.dataset.textJobFailed
            );
          } else {
            announce(root.dataset.textFinalized);
          }
          updateSummary();
        } else if (["discarded", "expired"].includes(status)) {
          stopPolling();
          clearPersistedBatch();
          batch = null;
          endpoints = {};
          finalized = false;
          input.disabled = false;
          updateSummary();
        }
      } catch (_error) {
        announce(root.dataset.textStatusError);
      }
    };

    function startPolling() {
      stopPolling();
      pollTimer = window.setInterval(() => void refreshBatch(), 10000);
    }

    function stopPolling() {
      if (pollTimer) window.clearInterval(pollTimer);
      pollTimer = null;
    }

    const restoreBatch = async () => {
      const queryId = new URL(window.location.href).searchParams.get("intake_batch");
      let stored = {};
      try {
        stored = parseJson(window.localStorage.getItem(storageKey));
      } catch (_error) {
        stored = {};
      }
      const id = queryId || stored.id;
      if (!id) return;
      batch = { id };
      endpoints = stored.id === id ? stored.endpoints || {} : {};
      endpoints = { ...normalizeEndpoints({}, id), ...endpoints };
      manifest.hidden = false;
      announce(root.dataset.textRestored);
      startPolling();
      await refreshBatch();
    };

    const finalizeBatch = async () => {
      if (finalize.disabled || !endpoints.finalize) return;
      finalize.disabled = true;
      try {
        const response = await window.fetch(endpoints.finalize, {
          method: "POST",
          credentials: "same-origin",
          headers: {
            Accept: "application/json",
            "X-CSRFToken": csrfToken(),
            "X-Requested-With": "XMLHttpRequest",
          },
        });
        const payload = parseJson(await response.text());
        if (!response.ok) throw new Error(errorMessage(payload, root.dataset.textFinalizeError));
        batch = responseBatch(payload);
        const id = batchId(batch);
        endpoints = normalizeEndpoints(payload, id);
        mergeServerItems(batch.items || []);
        finalized = true;
        persistBatch();
        startPolling();
        announce(root.dataset.textFinalized);
        input.disabled = true;
      } catch (error) {
        announce(error.message || root.dataset.textFinalizeError);
      }
      updateSummary();
    };

    const discardBatch = async () => {
      if (discard.disabled) return;
      if (!batch) {
        [...items.values()].forEach(removeLocalItem);
        announce(root.dataset.textDiscarded);
        updateSummary();
        return;
      }
      if (!endpoints.discard) {
        announce(root.dataset.textStatusError);
        return;
      }
      discard.disabled = true;
      try {
        const response = await window.fetch(endpoints.discard, {
          method: "POST",
          credentials: "same-origin",
          headers: {
            Accept: "application/json",
            "X-CSRFToken": csrfToken(),
            "X-Requested-With": "XMLHttpRequest",
          },
        });
        if (!response.ok) throw new Error("discard_failed");
        stopPolling();
        [...items.values()].forEach(removeLocalItem);
        batch = null;
        endpoints = {};
        finalized = false;
        clearPersistedBatch();
        announce(root.dataset.textDiscarded);
      } catch (_error) {
        announce(root.dataset.textStatusError);
      }
      updateSummary();
    };

    const startNewBatch = () => {
      if (!finalized || activeUploads > 0) return;
      stopPolling();
      [...items.values()].forEach(removeLocalItem);
      batch = null;
      endpoints = {};
      finalized = false;
      input.disabled = false;
      clearPersistedBatch();
      announce("");
      updateSummary();
      input.focus();
    };

    input.addEventListener("change", () => addFiles(input.files));
    ["dragenter", "dragover"].forEach((eventName) => {
      dropzone.addEventListener(eventName, (event) => {
        event.preventDefault();
        dropzone.classList.add("is-dragging");
      });
    });
    ["dragleave", "drop"].forEach((eventName) => {
      dropzone.addEventListener(eventName, (event) => {
        event.preventDefault();
        dropzone.classList.remove("is-dragging");
      });
    });
    dropzone.addEventListener("drop", (event) => {
      if (event.dataTransfer?.files) addFiles(event.dataTransfer.files);
    });
    retryAll?.addEventListener("click", () => {
      items.forEach((item) => {
        if (finalized || item.state !== "error" || !item.file) return;
        item.state = "queued";
        item.error = "";
        item.progress = 0;
        queue.push(item.localId);
        renderItem(item);
      });
      updateSummary();
      void pumpQueue();
    });
    receive?.addEventListener("click", () => {
      items.forEach((item) => {
        if (item.state === "queued" && item.file && item.title) {
          queue.push(item.localId);
        }
      });
      updateSummary();
      void pumpQueue();
    });
    newBatch?.addEventListener("click", startNewBatch);
    finalize?.addEventListener("click", () => void finalizeBatch());
    discard?.addEventListener("click", () => void discardBatch());
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) void refreshBatch();
    });
    window.addEventListener("beforeunload", (event) => {
      if (activeUploads === 0) return;
      event.preventDefault();
      event.returnValue = root.dataset.textUnsaved;
    });

    root.classList.add("is-enhanced");
    updateSummary();
    void restoreBatch();
  };

  document.addEventListener("DOMContentLoaded", () => {
    setupActionModal();
    setupConfirmModal();
    setupIntake();
  });
})();
