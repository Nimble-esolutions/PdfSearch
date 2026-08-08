(() => {
  "use strict";

  const page = document.querySelector("[data-settings-page]");
  if (!page) return;

  const labels = {
    yes: page.dataset.labelYes || "Yes",
    no: page.dataset.labelNo || "No",
    unknown: page.dataset.labelUnknown || "Unknown",
    healthy: page.dataset.labelHealthy || "Healthy",
    attention: page.dataset.labelAttention || "Needs attention",
    matches: page.dataset.labelMatches || "matching settings",
  };

  const setText = (selector, value) => {
    const element = document.querySelector(selector);
    if (element) element.textContent = value;
  };

  const booleanText = value => value === true ? labels.yes : value === false ? labels.no : labels.unknown;

  async function refreshRecovery(button) {
    const endpoint = page.dataset.settingsStatusEndpoint;
    if (!endpoint || button.disabled) return;
    const original = button.textContent;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");

    try {
      const response = await fetch(`${endpoint}?force=1`, {
        headers: {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
        credentials: "same-origin",
      });
      if (!response.ok) throw new Error("status_request_failed");
      const payload = await response.json();
      const status = payload.vault_status || {};
      setText("#vault-enabled", booleanText(status.enabled));
      setText("#vault-reachable", booleanText(status.reachable));
      setText("#vault-bucket-exists", booleanText(status.bucket_exists));
      setText("#vault-cache-age", `${Number(status.age_seconds || 0)}s`);
      setText("#vault-error", status.error && status.error !== "not_checked" ? status.error : "");
      document.getElementById("vault-error")?.classList.toggle(
        "d-none",
        !status.error || status.error === "not_checked",
      );

      const pill = document.getElementById("vault-status-pill");
      if (pill) {
        pill.textContent = status.healthy ? labels.healthy : labels.attention;
        pill.classList.toggle("settings-health--ok", Boolean(status.healthy));
        pill.classList.toggle("settings-health--attention", !status.healthy);
        pill.classList.remove("settings-health--neutral");
      }
    } catch (_error) {
      setText("#vault-error", page.dataset.labelRefreshFailed || "Status refresh failed. Current saved settings were not changed.");
      document.getElementById("vault-error")?.classList.remove("d-none");
    } finally {
      button.disabled = false;
      button.removeAttribute("aria-busy");
      button.textContent = original;
    }
  }

  document.querySelectorAll("[data-refresh-status='vault']").forEach(button => {
    button.addEventListener("click", () => refreshRecovery(button));
  });

  const filter = document.getElementById("settings-config-filter");
  const rows = Array.from(document.querySelectorAll("[data-configuration-row]"));
  const groups = Array.from(document.querySelectorAll("[data-configuration-group]"));
  const count = document.getElementById("settings-config-count");
  const empty = document.querySelector("[data-settings-empty]");

  function applyFilter() {
    const query = String(filter?.value || "").trim().toLocaleLowerCase();
    let visibleCount = 0;

    rows.forEach(row => {
      const visible = !query || String(row.dataset.search || "").includes(query);
      row.hidden = !visible;
      if (visible) visibleCount += 1;
    });

    groups.forEach(group => {
      const groupVisible = Boolean(group.querySelector("[data-configuration-row]:not([hidden])"));
      group.hidden = !groupVisible;
      if (query && groupVisible) group.open = true;
    });

    if (count) count.textContent = `${visibleCount} ${labels.matches}`;
    if (empty) empty.classList.toggle("d-none", visibleCount !== 0);
  }

  if (filter) {
    let timer = 0;
    filter.addEventListener("input", () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(applyFilter, 120);
    });
    applyFilter();
  }
})();
