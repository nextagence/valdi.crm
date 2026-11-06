/* Valdi CRM — клиентская логика (без зависимостей) */
(function () {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const NBSP = " ";

  /* ---------------------------------------------------------- навигация на мобильных */
  const toggle = $("[data-nav-toggle]");
  if (toggle) {
    const app = $("#app");
    toggle.addEventListener("click", (e) => {
      e.stopPropagation();
      const open = app.classList.toggle("nav-open");
      toggle.setAttribute("aria-expanded", String(open));
    });
    document.addEventListener("click", (e) => {
      if (app.classList.contains("nav-open") && !e.target.closest(".sidebar")) {
        app.classList.remove("nav-open");
        toggle.setAttribute("aria-expanded", "false");
      }
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") app.classList.remove("nav-open");
    });
  }

  /* ---------------------------------------------------------- уведомления */
  $$("[data-dismiss]").forEach((btn) =>
    btn.addEventListener("click", () => btn.closest(".flash").remove())
  );

  /* ---------------------------------------------------------- подтверждения */
  document.addEventListener("submit", (e) => {
    const msg = e.target.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) e.preventDefault();
  });

  /* ---------------------------------------------------------- кликабельные строки */
  document.addEventListener("click", (e) => {
    const row = e.target.closest("tr[data-href]");
    if (!row || e.target.closest("a, button, input, select, form, label")) return;
    if (window.getSelection && String(window.getSelection())) return; // выделяют текст
    if (e.metaKey || e.ctrlKey) window.open(row.dataset.href, "_blank");
    else window.location.href = row.dataset.href;
  });

  /* ---------------------------------------------------------- деньги */
  const parseNum = (v) => {
    const s = String(v || "").replace(/[\s ₸]/g, "").replace(",", ".");
    const n = Number(s);
    return Number.isFinite(n) ? n : 0;
  };
  const fmtMoney = (n) => {
    const cents = Math.round(n * 100);
    const whole = Math.trunc(cents / 100);
    const frac = String(Math.abs(cents % 100)).padStart(2, "0");
    return String(whole).replace(/\B(?=(\d{3})+(?!\d))/g, NBSP) + "," + frac + NBSP + "₸";
  };

  /* ---------------------------------------------------------- форма заказа */
  const orderForm = $("#order-form");
  if (orderForm) {
    // --- режим клиента
    const modeInput = $("[data-client-mode]", orderForm);
    const tabs = $$("[data-mode-tab]", orderForm);
    const setMode = (mode) => {
      modeInput.value = mode;
      tabs.forEach((t) => t.setAttribute("aria-selected", String(t.dataset.modeTab === mode)));
      $$("[data-mode-pane]", orderForm).forEach((p) => (p.hidden = p.dataset.modePane !== mode));
    };
    tabs.forEach((t) => t.addEventListener("click", () => setMode(t.dataset.modeTab)));

    // --- выбор клиента
    const picker = $("#picker");
    const idInput = $("[data-client-id]", picker);
    const list = $("[data-picker-list]", picker);
    const q = $("[data-picker-q]", picker);
    const selBox = $("[data-picker-selected]", picker);
    const searchBox = $("[data-picker-search]", picker);
    let recent = [];
    let selected = null;
    try { recent = JSON.parse(picker.dataset.recent || "[]"); } catch (_) {}
    try { selected = JSON.parse(picker.dataset.selected || "null"); } catch (_) {}

    const render = (items) => {
      list.innerHTML = "";
      if (!items.length) {
        const empty = document.createElement("div");
        empty.className = "picker-empty";
        empty.textContent = "Никого не нашли. Переключитесь на «Новый клиент», чтобы добавить.";
        list.appendChild(empty);
        return;
      }
      items.forEach((c) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "picker-item";
        b.setAttribute("role", "option");
        b.setAttribute("aria-selected", String(selected && selected.id === c.id));
        b.innerHTML = '<svg class="i i-sm" aria-hidden="true"><use href="#i-check"/></svg><span class="name"></span><span class="phone num"></span>';
        b.querySelector(".name").textContent = c.name;
        b.querySelector(".phone").textContent = c.phone;
        b.addEventListener("click", () => choose(c));
        list.appendChild(b);
      });
    };
    const showSelected = () => {
      if (selected) {
        idInput.value = selected.id;
        $("[data-sel-name]", selBox).textContent = selected.name;
        $("[data-sel-phone]", selBox).textContent = selected.phone;
        selBox.hidden = false;
        searchBox.hidden = true;
      } else {
        idInput.value = "";
        selBox.hidden = true;
        searchBox.hidden = false;
      }
    };
    const choose = (c) => { selected = c; showSelected(); };
    $("[data-picker-clear]", picker).addEventListener("click", () => {
      selected = null;
      showSelected();
      render(recent);
      q.focus();
    });

    let timer = null, seq = 0;
    q.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const term = q.value.trim();
        if (!term) return render(recent);
        const my = ++seq;
        try {
          const res = await fetch(picker.dataset.url + "?q=" + encodeURIComponent(term), { headers: { Accept: "application/json" } });
          if (!res.ok) throw new Error(res.status);
          const data = await res.json();
          if (my === seq) render(data);
        } catch (_) {
          list.innerHTML = '<div class="picker-empty">Поиск не ответил. Проверьте соединение и повторите.</div>';
        }
      }, 200);
    });
    q.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { // Enter выбирает первого найденного, а не отправляет форму
        e.preventDefault();
        const first = $(".picker-item", list);
        if (first) first.click();
      }
    });
    render(recent);
    showSelected();

    // --- позиции
    const itemsBox = $("#items");
    const tpl = $("#item-tpl");
    const grand = $("[data-grand-total]");
    const recalc = () => {
      let total = 0;
      $$("[data-item]", itemsBox).forEach((row) => {
        const qty = Math.max(0, Math.trunc(parseNum($("[data-qty]", row).value)));
        const price = parseNum($("[data-price]", row).value);
        const line = qty * price;
        total += line;
        $("[data-line-total]", row).textContent = fmtMoney(line);
      });
      grand.textContent = fmtMoney(total);
      const rows = $$("[data-item]", itemsBox);
      rows.forEach((r) => ($("[data-remove-item]", r).disabled = rows.length === 1));
    };
    itemsBox.addEventListener("input", recalc);
    itemsBox.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-remove-item]");
      if (!btn) return;
      const rows = $$("[data-item]", itemsBox);
      if (rows.length > 1) btn.closest("[data-item]").remove();
      recalc();
    });
    $("[data-add-item]").addEventListener("click", () => {
      itemsBox.appendChild(tpl.content.cloneNode(true));
      const last = $$("[data-item]", itemsBox).pop();
      $("select", last).focus();
      recalc();
    });
    recalc();
  }

  /* ---------------------------------------------------------- форма сотрудника */
  const empForm = $("#employee-form");
  if (empForm) {
    const role = $("[data-role]", empForm);
    const note = $("[data-admin-note]", empForm);
    const boxes = $$("[data-perms] input[type=checkbox]", empForm);
    let defaults = {};
    try { defaults = JSON.parse(empForm.dataset.roleDefaults || "{}"); } catch (_) {}
    const isNew = empForm.dataset.isNew === "1";
    let saved = boxes.filter((b) => b.checked && !b.disabled).map((b) => b.value);
    let prevRole = role.value;

    const setChecks = (list) => boxes.forEach((b) => (b.checked = list.includes(b.value)));
    const apply = () => {
      const isAdmin = role.value === "admin";
      if (isAdmin && prevRole !== "admin") saved = boxes.filter((b) => b.checked).map((b) => b.value);
      boxes.forEach((b) => {
        b.disabled = isAdmin;
        b.closest(".perm").classList.toggle("disabled", isAdmin);
      });
      note.hidden = !isAdmin;
      if (isAdmin) {
        boxes.forEach((b) => (b.checked = true));
      } else if (prevRole === "admin") {
        setChecks(saved.length ? saved : (defaults[role.value] || []));
      } else if (isNew) {
        // Для нового сотрудника роль подставляет типовой набор прав — его можно поправить вручную
        setChecks(defaults[role.value] || []);
      }
      prevRole = role.value;
    };
    role.addEventListener("change", apply);
  }
})();
