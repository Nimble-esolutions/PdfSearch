(() => {
  "use strict";

  const root = document.querySelector(".vault-workbench[data-state-url]");
  if (!root || !window.fetch) return;

  const update = (selector, value) => {
    const element = root.querySelector(selector);
    if (!element || value === undefined) return;
    element.textContent =
      value === null || String(value).trim() === "" ? "—" : String(value);
  };

  const refresh = async () => {
    if (document.hidden) return;
    try {
      const response = await fetch(root.dataset.stateUrl, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) return;
      const payload = await response.json();
      const state = payload.data || {};
      update(
        '[data-summary="runtime"]',
        state.authority?.runtime?.active_generation_id
      );
      update(
        '[data-summary="remote"]',
        state.authority?.remote?.authoritative_generation_id
      );
      update('[data-summary="sync"]', state.sync?.state);
      update('[data-summary="lease"]', state.lease?.state);
    } catch (_error) {
      // Static server-rendered evidence remains visible and truthful.
    }
  };

  window.setInterval(refresh, 15000);

  const highlightJob = () => {
    const params = new URLSearchParams(window.location.search);
    const jobId = params.get("job");
    if (!jobId) {
      return;
    }
    const selector = document.querySelector(`#maintenance-job-${jobId}`);
    if (!selector) {
      return;
    }
    selector.classList.add("maintenance-job--selected");
    selector.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  highlightJob();
})();
