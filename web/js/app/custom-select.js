// --- Custom themed <select> ---
// Progressive enhancement: the native <select> stays in the DOM as the single source of truth
// (so every existing `.value` read, `.value =` write, dynamic <option> population, and `change`
// listener keeps working untouched) but is visually hidden and replaced by a themed trigger +
// a body-appended dropdown panel that the modal's overflow:auto can't clip — the one thing plain
// CSS can't theme about a native select is its open popup list, which is what this replaces.
//
// Sync back into the custom UI is covered from three sides: the native `change` event (user or
// dispatched), a per-element MutationObserver on the <option> list (dynamic population), and an
// instance-level intercept of the `value`/`selectedIndex` setters (programmatic `.value =`, which
// fires no event). Excludes multi-line listboxes ([size]) and multi-selects ([multiple]).

(() => {
  const nativeValueDesc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value");
  const nativeIndexDesc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "selectedIndex");

  let openInstance = null; // the currently-open custom select, if any

  function closeOpen() {
    if (openInstance) openInstance.close();
  }

  function enhanceSelect(sel) {
    if (sel.dataset.csEnhanced) return;
    if (sel.multiple || sel.hasAttribute("size")) return;
    sel.dataset.csEnhanced = "true";

    const wrap = document.createElement("div");
    wrap.className = "custom-select";
    sel.parentNode.insertBefore(wrap, sel);
    wrap.appendChild(sel); // moves the node in-place; identity + getElementById unaffected
    sel.classList.add("cs-native");

    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "cs-trigger";
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.innerHTML =
      '<span class="cs-trigger-label"></span>' +
      '<svg class="cs-trigger-arrow" viewBox="0 0 10 6" aria-hidden="true"><path d="M0 0l5 6 5-6z" fill="currentColor"/></svg>';
    wrap.appendChild(trigger);
    const labelEl = trigger.querySelector(".cs-trigger-label");

    const panel = document.createElement("div");
    panel.className = "cs-panel";
    panel.setAttribute("role", "listbox");
    panel.hidden = true;

    const inst = { sel, wrap, trigger, panel, activeIndex: -1, close, open, isOpen: false };

    function syncLabel() {
      const opt = sel.options[sel.selectedIndex];
      const text = opt ? opt.textContent : "";
      labelEl.textContent = text || sel.dataset.placeholder || "Select…";
      trigger.dataset.placeholder = String(!text);
      trigger.disabled = sel.disabled;
      if (sel.disabled && inst.isOpen) close();
    }

    function buildPanel() {
      panel.innerHTML = "";
      [...sel.options].forEach((opt, i) => {
        const row = document.createElement("div");
        row.className = "cs-option";
        row.setAttribute("role", "option");
        row.textContent = opt.textContent;
        row.dataset.index = String(i);
        if (opt.disabled) row.dataset.disabled = "true";
        if (i === sel.selectedIndex) row.setAttribute("aria-selected", "true");
        row.addEventListener("mousedown", (e) => {
          e.preventDefault(); // keep focus on trigger; avoid blur-close race
          if (opt.disabled) return;
          pick(i);
        });
        panel.appendChild(row);
      });
    }

    function positionPanel() {
      const r = trigger.getBoundingClientRect();
      const margin = 6;
      panel.style.left = `${r.left}px`;
      panel.style.width = `${r.width}px`;
      // Measure height, then flip above if there isn't room below.
      panel.style.top = "0px";
      panel.style.maxHeight = "260px";
      const ph = Math.min(panel.scrollHeight + 8, 260);
      const below = window.innerHeight - r.bottom;
      if (below < ph + margin && r.top > below) {
        panel.style.top = `${Math.max(margin, r.top - ph - margin)}px`;
      } else {
        panel.style.top = `${r.bottom + margin}px`;
      }
    }

    function setActive(i) {
      const rows = panel.querySelectorAll(".cs-option");
      rows.forEach((r) => r.classList.remove("active"));
      inst.activeIndex = i;
      if (i >= 0 && rows[i]) {
        rows[i].classList.add("active");
        rows[i].scrollIntoView({ block: "nearest" });
      }
    }

    function open() {
      if (inst.isOpen || sel.disabled) return;
      closeOpen();
      buildPanel();
      document.body.appendChild(panel);
      panel.hidden = false;
      positionPanel();
      requestAnimationFrame(() => panel.classList.add("open"));
      wrap.classList.add("open");
      inst.isOpen = true;
      openInstance = inst;
      setActive(sel.selectedIndex);
    }

    function close() {
      if (!inst.isOpen) return;
      panel.classList.remove("open");
      wrap.classList.remove("open");
      inst.isOpen = false;
      if (openInstance === inst) openInstance = null;
      setTimeout(() => { if (!inst.isOpen) { panel.hidden = true; panel.remove(); } }, 130);
    }

    function pick(i) {
      const prev = sel.selectedIndex;
      sel.selectedIndex = i; // routes through the intercepted setter -> syncLabel()
      close();
      trigger.focus();
      if (i !== prev) {
        sel.dispatchEvent(new Event("input", { bubbles: true }));
        sel.dispatchEvent(new Event("change", { bubbles: true }));
      }
    }

    trigger.addEventListener("click", () => { inst.isOpen ? close() : open(); });
    trigger.addEventListener("keydown", (e) => {
      if (!inst.isOpen) {
        if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) { e.preventDefault(); open(); }
        return;
      }
      const count = sel.options.length;
      if (e.key === "Escape") { e.preventDefault(); close(); }
      else if (e.key === "ArrowDown") { e.preventDefault(); setActive(Math.min(count - 1, inst.activeIndex + 1)); }
      else if (e.key === "ArrowUp") { e.preventDefault(); setActive(Math.max(0, inst.activeIndex - 1)); }
      else if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        const opt = sel.options[inst.activeIndex];
        if (opt && !opt.disabled) pick(inst.activeIndex);
      }
    });

    // --- keep the custom UI in sync with native-side changes ---
    Object.defineProperty(sel, "value", {
      configurable: true,
      get() { return nativeValueDesc.get.call(this); },
      set(v) { nativeValueDesc.set.call(this, v); syncLabel(); },
    });
    Object.defineProperty(sel, "selectedIndex", {
      configurable: true,
      get() { return nativeIndexDesc.get.call(this); },
      set(v) { nativeIndexDesc.set.call(this, v); syncLabel(); },
    });
    sel.addEventListener("change", syncLabel);
    new MutationObserver(() => syncLabel()).observe(sel, { childList: true, attributes: true, attributeFilter: ["disabled"] });

    syncLabel();
  }

  function initCustomSelects(root = document) {
    root.querySelectorAll("select:not([size]):not([multiple])").forEach(enhanceSelect);
  }
  // Exposed so a future dynamically-built select can be themed on demand.
  window.enhanceSelect = enhanceSelect;
  window.initCustomSelects = initCustomSelects;

  // Global dismiss handlers.
  document.addEventListener("mousedown", (e) => {
    if (openInstance && !e.target.closest(".custom-select") && !e.target.closest(".cs-panel")) closeOpen();
  });
  window.addEventListener("resize", closeOpen);
  document.addEventListener("scroll", closeOpen, true);

  // Static selects exist by the time these end-of-body scripts run.
  initCustomSelects();
})();
