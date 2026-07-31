(() => {
  const icons = () => window.lucide?.createIcons({ attrs: { "stroke-width": 2 } });
  const pendingToasts = new WeakMap();
  const dismissToast = (toast) => {
    if (!toast?.isConnected) return;
    toast.classList.add("leaving");
    window.setTimeout(() => toast.remove(), 180);
  };
  const showToast = (message, tone = "info", timeout = 4200) => {
    const region = document.getElementById("toast-region");
    if (!region || !message) return null;
    const toast = document.createElement("div");
    toast.className = `operation-toast ${tone}`;
    toast.setAttribute("role", tone === "error" ? "alert" : "status");
    const marker = document.createElement("span");
    marker.className = "toast-marker";
    const label = document.createElement("span");
    label.textContent = message;
    toast.append(marker, label);
    region.append(toast);
    if (timeout) window.setTimeout(() => dismissToast(toast), timeout);
    return toast;
  };
  const requestElement = (event) => event.detail?.elt || event.target;
  const setupBulkSelection = (formId, selectAllId, itemClass, countId, submitId) => {
    const form = document.getElementById(formId);
    if (!form || form.dataset.selectionReady === "true") return;
    form.dataset.selectionReady = "true";
    const selectAll = form.querySelector(`#${selectAllId}`);
    const checkboxes = [...form.querySelectorAll(`.${itemClass}`)];
    const count = form.querySelector(`#${countId}`);
    const download = form.querySelector(`#${submitId}`);
    const update = () => {
      const selected = checkboxes.filter((checkbox) => checkbox.checked).length;
      count.textContent = `${selected} selected`;
      download.disabled = selected === 0;
      selectAll.checked = checkboxes.length > 0 && selected === checkboxes.length;
      selectAll.indeterminate = selected > 0 && selected < checkboxes.length;
    };
    selectAll.addEventListener("change", () => {
      checkboxes.forEach((checkbox) => { checkbox.checked = selectAll.checked; });
      update();
    });
    checkboxes.forEach((checkbox) => checkbox.addEventListener("change", update));
    update();
  };
  const setupNicheSelection = () => {
    setupBulkSelection("niche-export-form", "select-all-niches", "niche-checkbox", "selected-niche-count", "download-selected-niches");
    setupBulkSelection("state-export-form", "select-all-states", "state-checkbox", "selected-state-count", "download-selected-states");
  };

  document.addEventListener("DOMContentLoaded", () => { icons(); setupNicheSelection(); });
  document.body.addEventListener("htmx:afterSwap", () => { icons(); setupNicheSelection(); });
  document.body.addEventListener("htmx:beforeRequest", (event) => {
    const element = requestElement(event);
    const message = element?.dataset?.pendingMessage;
    if (!message) return;
    element.setAttribute("aria-busy", "true");
    pendingToasts.set(element, showToast(message, "working", 0));
  });
  document.body.addEventListener("htmx:afterRequest", (event) => {
    const element = requestElement(event);
    if (!element) return;
    element.removeAttribute("aria-busy");
    dismissToast(pendingToasts.get(element));
    pendingToasts.delete(element);
    if (event.detail?.failed) {
      showToast("The operation failed; try again", "error", 6000);
      return;
    }
    const message = element.dataset?.successMessage;
    if (message) showToast(message, "success");
  });
  document.body.addEventListener("htmx:timeout", () => {
    showToast("The operation timed out; try again", "error", 6000);
  });
  document.body.addEventListener("htmx:sendError", () => {
    showToast("The dashboard could not be reached", "error", 6000);
  });
})();
