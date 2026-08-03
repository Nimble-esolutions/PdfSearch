(function () {
  "use strict";

  document.querySelectorAll("[data-maintenance-scope]").forEach(function (scope) {
    const filter = scope.querySelector("[data-maintenance-scope-filter]");
    const items = Array.from(scope.querySelectorAll("[data-maintenance-scope-item]"));
    const status = scope.querySelector("[data-maintenance-scope-status]");
    const selectVisible = scope.querySelector("[data-maintenance-select-visible]");
    const clear = scope.querySelector("[data-maintenance-clear]");

    function visibleItems() {
      return items.filter(function (item) { return !item.hidden; });
    }

    function updateStatus() {
      const selected = items.filter(function (item) {
        return item.querySelector('input[type="checkbox"]').checked;
      }).length;
      const visible = visibleItems().length;
      status.textContent = selected + " " + scope.dataset.selectedLabel + " · " +
        visible + " " + scope.dataset.visibleLabel + " · " +
        items.length + " " + scope.dataset.totalLabel;
    }

    function applyFilter() {
      const query = filter.value.trim().toLocaleLowerCase();
      items.forEach(function (item) {
        item.hidden = Boolean(query) && !item.textContent.toLocaleLowerCase().includes(query);
      });
      updateStatus();
    }

    filter.addEventListener("input", applyFilter);
    scope.addEventListener("change", updateStatus);
    selectVisible.addEventListener("click", function () {
      visibleItems().forEach(function (item) {
        item.querySelector('input[type="checkbox"]').checked = true;
      });
      updateStatus();
    });
    clear.addEventListener("click", function () {
      items.forEach(function (item) {
        item.querySelector('input[type="checkbox"]').checked = false;
      });
      updateStatus();
    });

    updateStatus();
  });
}());
