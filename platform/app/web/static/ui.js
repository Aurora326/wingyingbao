/* Friendly UI helpers: modals, plain-language scheduling, quick filters. */

function openModal(id, focusSelector) {
  const modal = document.getElementById(id);
  if (!modal) return;
  modal.classList.add("open");
  document.body.classList.add("modal-open");
  const target = focusSelector
    ? modal.querySelector(focusSelector)
    : modal.querySelector("input:not([type=hidden]), select, textarea, button");
  if (target) setTimeout(() => target.focus(), 40);
}

function closeModal(id) {
  const modal = document.getElementById(id);
  if (!modal) return;
  modal.classList.remove("open");
  if (!document.querySelector(".modal.open")) document.body.classList.remove("modal-open");
}

function openAccountModal() {
  const form = document.getElementById("account-form");
  if (form) form.reset();
  const accountId = document.getElementById("account-id");
  if (accountId) accountId.value = "";
  const title = document.getElementById("account-modal-title");
  if (title) title.textContent = "添加银行账户";
  const submit = document.getElementById("account-submit");
  if (submit) submit.textContent = "创建账户";
  const bank = document.getElementById("account-bank");
  if (bank) bank.disabled = false;
  const accountNo = document.getElementById("account-no");
  if (accountNo) accountNo.readOnly = false;
  const note = document.getElementById("credential-edit-note");
  if (note) note.hidden = true;
  const enabledField = document.getElementById("account-enabled-field");
  if (enabledField) enabledField.style.display = "";
  const enabledBox = document.getElementById("account-collect-enabled");
  if (enabledBox) enabledBox.checked = false;
  if (form) {
    form.username.placeholder = "登录企业网银的账号";
    form.password.placeholder = "";
    form.pin.placeholder = "";
  }
  if (bank && typeof filterAccountDevices === "function") filterAccountDevices(bank.value);
  const preset = document.getElementById("schedule-preset");
  if (preset) preset.value = "0 8,18 * * *";
  applySchedulePreset(preset ? preset.value : "0 8,18 * * *");
  const collectMode = document.getElementById("account-collect-mode");
  if (collectMode) collectMode.value = "both";
  openModal("account-modal", "#account-bank");
}

function openDeviceModal() {
  openModal("device-modal", "#device-name");
}

function switchPane(name, button) {
  document.querySelectorAll("[data-pane]").forEach((pane) => {
    pane.classList.toggle("hidden", pane.dataset.pane !== name);
  });
  document.querySelectorAll("[data-pane-target]").forEach((tab) => {
    const active = tab.dataset.paneTarget === name;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
  });
  const search = document.getElementById("device-search");
  if (name === "devices" && search) filterDeviceRows(search.value);
}

function togglePassword(inputId, button) {
  const input = document.getElementById(inputId);
  if (!input) return;
  const show = input.type === "password";
  input.type = show ? "text" : "password";
  button.textContent = show ? "隐藏" : "显示";
  button.setAttribute("aria-label", show ? "隐藏密码" : "显示密码");
}

function describeCron(cron) {
  const parts = (cron || "").trim().split(/\s+/);
  if (parts.length !== 5 || parts[2] !== "*" || parts[3] !== "*" || parts[4] !== "*") {
    return cron ? "自定义计划" : "未设置";
  }
  if (parts[0] !== "0") return "自定义时间";
  if (parts[1].startsWith("*/")) return `每 ${parts[1].slice(2)} 小时`;
  if (parts[1].includes(",")) {
    return "每天 " + parts[1].split(",").map((h) => `${String(h).padStart(2, "0")}:00`).join("、");
  }
  if (parts[1] === "*") return "每小时";
  return `每天 ${String(parts[1]).padStart(2, "0")}:00`;
}

function applySchedulePreset(value) {
  const input = document.getElementById("cron-input");
  const wrap = document.getElementById("cron-custom-wrap");
  const preview = document.getElementById("cron-preview");
  if (!input) return;
  if (value === "custom") {
    if (wrap) wrap.hidden = false;
    input.readOnly = false;
    input.focus();
  } else {
    input.value = value;
    input.readOnly = true;
    if (wrap) wrap.hidden = true;
  }
  if (preview) preview.textContent = describeCron(input.value);
}

function filterDeviceRows(query) {
  const keyword = (query || "").trim().toLowerCase();
  const rows = document.querySelectorAll("#device-table tbody tr[data-search]");
  let visible = 0;
  rows.forEach((row) => {
    const hit = !keyword || row.dataset.search.includes(keyword);
    row.classList.toggle("hidden", !hit);
    if (hit) visible += 1;
  });
  const count = document.getElementById("device-count-visible");
  if (count) count.textContent = visible;
  if (typeof refreshPagers === "function") refreshPagers(true);
}

function sanitizeFilterForm(form) {
  Array.from(form.elements).forEach((el) => {
    if (el.name && el.tagName !== "BUTTON" && el.value === "") el.disabled = true;
  });
}

function quickRange(kind) {
  const form = document.querySelector('form[data-filter-form]');
  if (!form) return;
  const from = form.querySelector('[name="date_from"]');
  const to = form.querySelector('[name="date_to"]');
  const pad = (n) => String(n).padStart(2, "0");
  const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const today = new Date();
  const start = new Date(today);
  if (kind === "7") start.setDate(today.getDate() - 6);
  if (kind === "30") start.setDate(today.getDate() - 29);
  if (kind === "month") start.setDate(1);
  if (kind === "all") {
    if (from) from.value = "";
    if (to) to.value = "";
  } else {
    if (from) from.value = iso(start);
    if (to) to.value = iso(today);
  }
  document.querySelectorAll("[data-range]").forEach((el) => {
    el.classList.toggle("active", el.dataset.range === kind);
  });
  sanitizeFilterForm(form);
  form.submit();
}

function copyText(text, message) {
  const done = () => toast(message || "已复制");
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
  } else {
    fallbackCopy(text, done);
  }
}

function fallbackCopy(text, done) {
  const el = document.createElement("textarea");
  el.value = text;
  el.style.position = "fixed";
  el.style.opacity = "0";
  document.body.appendChild(el);
  el.select();
  try {
    document.execCommand("copy");
    done();
  } catch (error) {
    toast("复制失败，请手动选择");
  }
  el.remove();
}

function initClock() {
  const now = new Date();
  const week = ["日", "一", "二", "三", "四", "五", "六"];
  const label = `${now.getMonth() + 1} 月 ${now.getDate()} 日 · 星期${week[now.getDay()]}`;
  document.querySelectorAll("[data-today]").forEach((el) => {
    el.textContent = label;
  });
  let greeting = "你好";
  const hour = now.getHours();
  if (hour < 6) greeting = "夜深了";
  else if (hour < 11) greeting = "早上好";
  else if (hour < 13) greeting = "中午好";
  else if (hour < 18) greeting = "下午好";
  else greeting = "晚上好";
  document.querySelectorAll("[data-greeting]").forEach((el) => {
    el.textContent = greeting;
  });
}

function initFileDrops() {
  document.querySelectorAll('.file-drop input[type="file"]').forEach((input) => {
    input.addEventListener("change", () => {
      const drop = input.closest(".file-drop");
      const text = drop && drop.querySelector("span");
      if (text) {
        text.textContent = input.files && input.files[0]
          ? `已选择：${input.files[0].name}`
          : "选择 Excel / CSV 文件";
      }
    });
  });
}

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  document.querySelectorAll(".modal.open").forEach((modal) => closeModal(modal.id));
});

document.addEventListener("click", (event) => {
  const modal = event.target.closest(".modal");
  if (modal && event.target === modal) closeModal(modal.id);
});

document.addEventListener("submit", (event) => {
  const form = event.target;
  if (form && form.matches && form.matches("form[data-filter-form]")) sanitizeFilterForm(form);
}, true);

document.addEventListener("DOMContentLoaded", () => {
  initClock();
  initFileDrops();
  const activeNav = document.querySelector(".nav a.active");
  if (activeNav && window.innerWidth <= 900) {
    activeNav.scrollIntoView({ block: "nearest", inline: "center" });
  }
  const preset = document.getElementById("schedule-preset");
  if (preset) applySchedulePreset(preset.value);
  const search = document.getElementById("device-search");
  if (search) filterDeviceRows(search.value);
});
