async function postJSON(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : null,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  return response.json();
}

async function patchJSON(url, body) {
  const response = await fetch(url, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : null,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  return response.json();
}

async function getJSON(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(response.statusText);
  return response.json();
}

function toast(message) {
  const el = document.getElementById("toast");
  if (!el) return alert(message);
  el.textContent = message;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 2600);
}

async function collectNow(accountId, mode) {
  const suffix = mode ? `?type=${encodeURIComponent(mode)}` : "";
  try {
    await postJSON(`/api/accounts/${accountId}/collect${suffix}`);
    toast("已加入采集队列");
    setTimeout(() => location.reload(), 800);
  } catch (error) {
    toast("触发失败: " + error.message);
  }
}

async function retryTask(taskId) {
  try {
    await postJSON(`/api/tasks/${taskId}/retry`);
    toast("已重新入队");
    setTimeout(() => location.reload(), 800);
  } catch (error) {
    toast("重试失败: " + error.message);
  }
}

let confirmHandler = null;

function openConfirmModal({ title = "确认操作", message = "", confirmText = "确定", danger = false, onConfirm }) {
  const titleEl = document.getElementById("confirm-title");
  const messageEl = document.getElementById("confirm-message");
  const ok = document.getElementById("confirm-ok");
  if (!titleEl || !messageEl || !ok) return;
  titleEl.textContent = title;
  messageEl.textContent = message;
  ok.textContent = confirmText;
  ok.className = danger ? "danger" : "primary";
  ok.disabled = false;
  confirmHandler = onConfirm;
  openModal("confirm-modal", "#confirm-ok");
}

function closeConfirmModal() {
  confirmHandler = null;
  closeModal("confirm-modal");
}

async function confirmModalOk() {
  const handler = confirmHandler;
  if (!handler) return;
  const ok = document.getElementById("confirm-ok");
  const label = ok.textContent;
  ok.disabled = true;
  ok.textContent = "处理中…";
  closeModal("confirm-modal");
  confirmHandler = null;
  try {
    await handler();
  } catch (error) {
    toast("操作失败: " + error.message);
  } finally {
    ok.disabled = false;
    ok.textContent = label;
  }
}

function stopTask(taskId) {
  openConfirmModal({
    title: "停止采集",
    message: "确定停止这次采集吗？正在执行的采集会被中断。",
    confirmText: "停止",
    danger: true,
    onConfirm: async () => {
      await postJSON(`/api/tasks/${taskId}/stop`);
      toast("已请求停止");
      setTimeout(() => location.reload(), 800);
    },
  });
}

let deleteAccountContext = null;

function openDeleteAccountModal(button) {
  const data = button.dataset;
  deleteAccountContext = {
    id: data.id,
    accountNo: data.accountNo,
    label: data.label,
    statements: Number(data.statements || 0),
    receipts: Number(data.receipts || 0),
    artifacts: Number(data.artifacts || 0),
    tasks: Number(data.tasks || 0),
    active: Number(data.active || 0),
    enabled: data.enabled === "1",
  };
  const ctx = deleteAccountContext;
  document.getElementById("delete-account-label").textContent = ctx.label;
  document.getElementById("delete-statements").textContent = `${ctx.statements} 条`;
  document.getElementById("delete-receipts").textContent = `${ctx.receipts} 份`;
  document.getElementById("delete-artifacts").textContent = `${ctx.artifacts} 个`;
  document.getElementById("delete-tasks").textContent = `${ctx.tasks} 条`;

  const warning = document.getElementById("delete-account-warning");
  let text = "该账户没有流水或回单，删除后无法恢复。";
  if (ctx.active) {
    text = "该账户有正在执行的采集任务，暂时不能删除。请等待任务结束后再试。";
  } else if (ctx.statements || ctx.receipts || ctx.artifacts) {
    text = `该账户已有 ${ctx.statements} 条流水、${ctx.receipts} 份回单、${ctx.artifacts} 个原始文件。删除会一并清除，且无法恢复；建议先停用采集。`;
  }
  warning.textContent = text;
  warning.classList.toggle("warn", Boolean(ctx.active || ctx.statements || ctx.receipts || ctx.artifacts));

  const toggle = document.getElementById("delete-account-toggle");
  toggle.textContent = ctx.enabled ? "停用采集" : "启用采集";
  toggle.disabled = false;

  const input = document.getElementById("delete-account-confirm");
  input.value = "";
  input.oninput = updateDeleteAccountSubmit;
  updateDeleteAccountSubmit();
  openModal("delete-account-modal", "#delete-account-confirm");
}

function updateDeleteAccountSubmit() {
  const ctx = deleteAccountContext;
  const input = document.getElementById("delete-account-confirm");
  const submit = document.getElementById("delete-account-submit");
  if (!ctx || !input || !submit) return;
  submit.disabled = Boolean(ctx.active) || input.value !== ctx.accountNo;
}

async function deleteAccountRequest(id, confirm) {
  const response = await fetch(`/api/accounts/${id}?confirm=${encodeURIComponent(confirm)}`, {
    method: "DELETE",
  });
  let data = {};
  try {
    data = await response.json();
  } catch (error) {
    data = {};
  }
  if (!response.ok) throw new Error(data.detail || response.statusText);
  return data;
}

async function confirmDeleteAccount() {
  const ctx = deleteAccountContext;
  if (!ctx) return;
  const input = document.getElementById("delete-account-confirm");
  const submit = document.getElementById("delete-account-submit");
  if (!input || input.value !== ctx.accountNo) {
    toast("请输入完整账号确认");
    return;
  }
  submit.disabled = true;
  submit.textContent = "删除中…";
  try {
    await deleteAccountRequest(ctx.id, input.value);
    toast("账户已删除");
    setTimeout(() => location.reload(), 600);
  } catch (error) {
    toast("删除失败: " + error.message);
    submit.textContent = "确认删除";
    updateDeleteAccountSubmit();
  }
}

async function toggleAccountFromModal() {
  const ctx = deleteAccountContext;
  if (!ctx) return;
  const button = document.getElementById("delete-account-toggle");
  button.disabled = true;
  try {
    const result = await postJSON(`/api/accounts/${ctx.id}/toggle`);
    toast(result.collect_enabled ? "已启用采集" : "已停用采集");
    setTimeout(() => location.reload(), 600);
  } catch (error) {
    toast("操作失败: " + error.message);
    button.disabled = false;
  }
}

const LOG_LEVELS = {
  TRACE: { label: "调试", cls: "debug" },
  DEBUG: { label: "调试", cls: "debug" },
  INFO: { label: "信息", cls: "info" },
  SUCCESS: { label: "成功", cls: "success" },
  WARNING: { label: "警告", cls: "warn" },
  ERROR: { label: "错误", cls: "error" },
  CRITICAL: { label: "错误", cls: "error" },
  OTHER: { label: "其他", cls: "other" },
};

const logState = {
  taskId: null,
  runs: [],
  entries: [],
  filtered: [],
  page: 1,
  pageSize: 10,
  attempt: "",
};

function logLevelMeta(level) {
  return LOG_LEVELS[level] || LOG_LEVELS.OTHER;
}

function logDuration(ms) {
  const value = Number(ms || 0);
  return value >= 1000 ? `${(value / 1000).toFixed(1)} 秒` : `${value} 毫秒`;
}

function showLogMessage(message) {
  const table = document.querySelector(".log-viewer-table");
  const empty = document.getElementById("log-empty");
  const pager = document.getElementById("log-pager");
  const total = document.getElementById("log-total");
  const download = document.getElementById("log-download");
  const tbody = document.getElementById("log-rows");
  if (tbody) tbody.replaceChildren();
  if (table) table.hidden = true;
  if (empty) {
    empty.hidden = false;
    empty.textContent = message;
  }
  if (pager) pager.hidden = true;
  if (total) total.textContent = "0";
  if (download) download.disabled = true;
}

function populateLogAttempts() {
  const select = document.getElementById("log-attempt");
  if (!select) return;
  select.replaceChildren();
  if (logState.runs.length > 1) {
    const option = document.createElement("option");
    option.value = "all";
    option.textContent = `全部执行记录（${logState.runs.length} 次）`;
    select.appendChild(option);
  }
  logState.runs.forEach((run, index) => {
    const option = document.createElement("option");
    option.value = String(run.attempt);
    const status = run.exit_code === 0 ? "成功" : `exit=${run.exit_code ?? "—"}`;
    option.textContent = `${index === 0 ? "最近一次 · " : ""}第 ${run.attempt} 次执行 · ${status} · ${logDuration(run.duration_ms)}`;
    select.appendChild(option);
  });
  select.hidden = logState.runs.length <= 1;
}

async function showLog(taskId) {
  const title = document.getElementById("log-title");
  const subtitle = document.getElementById("log-subtitle");
  const search = document.getElementById("log-search");
  const level = document.getElementById("log-level");
  const pageSize = document.getElementById("log-page-size");
  const jump = document.getElementById("log-jump");
  if (title) title.textContent = `任务 #${taskId} 执行日志`;
  if (subtitle) subtitle.textContent = "供技术人员排查问题，日常使用无需查看";
  if (search) search.value = "";
  if (level) level.value = "";
  if (pageSize) pageSize.value = "10";
  if (jump) jump.value = "";
  logState.taskId = taskId;
  logState.runs = [];
  logState.entries = [];
  logState.filtered = [];
  logState.page = 1;
  logState.pageSize = 10;
  logState.attempt = "";
  showLogMessage("加载中…");
  openModal("log-modal", "#log-search");
  try {
    const data = await getJSON(`/api/tasks/${taskId}/log`);
    logState.runs = data.items || [];
    if (!logState.runs.length) {
      showLogMessage("暂无执行记录");
      return;
    }
    populateLogAttempts();
    logState.entries = [];
    logState.runs.forEach((run) => {
      (run.entries || []).forEach((entry) => {
        logState.entries.push({ ...entry, attempt: run.attempt });
      });
    });
    const latest = logState.runs[0];
    logState.attempt = String(latest.attempt);
    const attemptSelect = document.getElementById("log-attempt");
    if (attemptSelect) attemptSelect.value = logState.attempt;
    if (subtitle) {
      const prefix = logState.runs.length > 1 ? `共 ${logState.runs.length} 次执行，默认显示最近一次 · ` : "";
      subtitle.textContent = `${prefix}第 ${latest.attempt} 次 · 退出码 ${latest.exit_code ?? "—"} · 耗时 ${logDuration(latest.duration_ms)}`;
    }
    applyLogFilters();
  } catch (error) {
    showLogMessage("读取失败: " + error.message);
  }
}

function applyLogFilters() {
  const search = (document.getElementById("log-search")?.value || "").trim().toLowerCase();
  const level = document.getElementById("log-level")?.value || "";
  const attempt = document.getElementById("log-attempt")?.value || logState.attempt;
  const levels = level === "ERROR" ? ["ERROR", "CRITICAL"] : level === "DEBUG" ? ["DEBUG", "TRACE"] : [level];
  logState.filtered = logState.entries.filter((entry) => {
    if (attempt && attempt !== "all" && String(entry.attempt) !== attempt) return false;
    if (level && !levels.includes(entry.level)) return false;
    if (search) {
      const haystack = `${entry.time || ""} ${entry.level || ""} ${entry.source || ""} ${entry.text || ""}`.toLowerCase();
      if (!haystack.includes(search)) return false;
    }
    return true;
  });
  logState.page = 1;
  renderLogPage();
}

function logPageNumbers(current, total) {
  if (total <= 7) return Array.from({ length: total }, (_, index) => index + 1);
  let start = Math.max(2, current - 1);
  let end = Math.min(total - 1, current + 1);
  if (current <= 3) {
    start = 2;
    end = 4;
  }
  if (current >= total - 2) {
    start = total - 3;
    end = total - 1;
  }
  const pages = [1];
  if (start > 2) pages.push("…");
  for (let page = start; page <= end; page += 1) pages.push(page);
  if (end < total - 1) pages.push("…");
  pages.push(total);
  return pages;
}

function logPageButton(label, page, options = {}) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `log-page${options.active ? " active" : ""}`;
  button.textContent = label;
  button.disabled = Boolean(options.disabled);
  if (options.active) button.setAttribute("aria-current", "page");
  button.addEventListener("click", () => gotoLogPage(page));
  return button;
}

function renderLogPage() {
  const tbody = document.getElementById("log-rows");
  const table = document.querySelector(".log-viewer-table");
  const empty = document.getElementById("log-empty");
  const pager = document.getElementById("log-pager");
  const total = document.getElementById("log-total");
  const pages = document.getElementById("log-pages");
  const download = document.getElementById("log-download");
  const jump = document.getElementById("log-jump");
  if (!tbody || !table || !empty || !pager || !total || !pages) return;

  const totalPages = Math.max(1, Math.ceil(logState.filtered.length / logState.pageSize));
  logState.page = Math.min(Math.max(1, logState.page), totalPages);
  const start = (logState.page - 1) * logState.pageSize;
  const rows = logState.filtered.slice(start, start + logState.pageSize);

  total.textContent = String(logState.filtered.length);
  if (download) download.disabled = logState.filtered.length === 0;
  if (jump) {
    jump.max = String(totalPages);
    jump.placeholder = `1-${totalPages}`;
  }

  if (!rows.length) {
    showLogMessage(logState.entries.length ? "没有匹配的日志，换个关键词或级别试试" : "暂无执行记录");
    return;
  }

  table.hidden = false;
  empty.hidden = true;
  tbody.replaceChildren();
  const showAttempt = (document.getElementById("log-attempt")?.value || "") === "all";
  const fragment = document.createDocumentFragment();
  rows.forEach((entry) => {
    const meta = logLevelMeta(entry.level);
    const tr = document.createElement("tr");

    const when = document.createElement("td");
    when.className = "when";
    when.textContent = entry.time || "—";
    if (showAttempt) {
      const attempt = document.createElement("div");
      attempt.className = "log-attempt";
      attempt.textContent = `第 ${entry.attempt} 次`;
      when.appendChild(attempt);
    }

    const content = document.createElement("td");
    content.className = "log-content";
    const message = document.createElement("div");
    message.className = "log-msg";
    message.textContent = entry.text || "—";
    content.appendChild(message);
    if (entry.source) {
      const source = document.createElement("div");
      source.className = "log-source";
      source.textContent = entry.source;
      content.appendChild(source);
    }

    const levelCell = document.createElement("td");
    const tag = document.createElement("span");
    tag.className = `log-level lv-${meta.cls}`;
    tag.textContent = meta.label;
    levelCell.appendChild(tag);

    tr.append(when, content, levelCell);
    fragment.appendChild(tr);
  });
  tbody.appendChild(fragment);

  pager.hidden = false;
  pages.replaceChildren();
  pages.appendChild(logPageButton("‹", logState.page - 1, { disabled: logState.page <= 1 }));
  logPageNumbers(logState.page, totalPages).forEach((page) => {
    if (page === "…") {
      const gap = document.createElement("span");
      gap.className = "log-page-gap";
      gap.textContent = "…";
      pages.appendChild(gap);
    } else {
      pages.appendChild(logPageButton(String(page), page, { active: page === logState.page }));
    }
  });
  pages.appendChild(logPageButton("›", logState.page + 1, { disabled: logState.page >= totalPages }));
}

function gotoLogPage(page) {
  const totalPages = Math.max(1, Math.ceil(logState.filtered.length / logState.pageSize));
  logState.page = Math.min(Math.max(1, page), totalPages);
  renderLogPage();
}

function onLogFilterChange() {
  applyLogFilters();
}

function onLogPageSizeChange() {
  const value = parseInt(document.getElementById("log-page-size")?.value || "10", 10);
  logState.pageSize = [10, 20, 50, 100].includes(value) ? value : 10;
  logState.page = 1;
  renderLogPage();
}

function onLogJump() {
  const input = document.getElementById("log-jump");
  if (!input) return;
  const page = parseInt(input.value || "1", 10);
  input.value = "";
  if (Number.isFinite(page)) gotoLogPage(page);
}

function downloadLog() {
  if (!logState.filtered.length) return;
  const lines = logState.filtered.map((entry) => {
    const source = entry.source ? `${entry.source} - ` : "";
    return `${entry.time || ""} | ${entry.level || ""} | ${source}${entry.text || ""}`;
  });
  const header = `任务 #${logState.taskId} 执行日志\n\n`;
  const blob = new Blob([header, lines.join("\n")], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `task_${logState.taskId}_log.txt`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function closeLog() {
  closeModal("log-modal");
}

async function confirmReconItem(itemId, selectId) {
  const select = document.getElementById(selectId);
  const docId = select && select.value ? parseInt(select.value, 10) : null;
  try {
    await postJSON(`/api/recon/items/${itemId}/confirm`, { internal_doc_id: docId });
    toast("已确认匹配");
    setTimeout(() => location.reload(), 600);
  } catch (error) {
    toast("操作失败: " + error.message);
  }
}

async function ignoreReconItem(itemId) {
  try {
    await postJSON(`/api/recon/items/${itemId}/ignore`);
    toast("已忽略");
    setTimeout(() => location.reload(), 600);
  } catch (error) {
    toast("操作失败: " + error.message);
  }
}

function setAccountSchedule(cron) {
  const select = document.getElementById("schedule-preset");
  const input = document.getElementById("cron-input");
  if (!select || !input) return;
  const value = cron || "0 8,18 * * *";
  const known = Array.from(select.options).some((option) => option.value === value);
  if (known) {
    select.value = value;
    applySchedulePreset(value);
    return;
  }
  select.value = "custom";
  input.value = value;
  applySchedulePreset("custom");
}

function openEditAccountModal(button) {
  const form = document.getElementById("account-form");
  if (form) form.reset();
  document.getElementById("account-id").value = button.dataset.id || "";
  document.getElementById("account-modal-title").textContent = "编辑银行账户";
  document.getElementById("account-submit").textContent = "保存修改";

  const bank = document.getElementById("account-bank");
  bank.value = button.dataset.bank || "";
  bank.disabled = true;
  const accountNo = document.getElementById("account-no");
  accountNo.value = button.dataset.accountNo || "";
  accountNo.readOnly = true;
  document.getElementById("account-name-input").value = button.dataset.accountName || "";

  const note = document.getElementById("credential-edit-note");
  if (note) note.hidden = false;
  form.username.value = "";
  form.password.value = "";
  form.pin.value = "";
  form.username.placeholder = "留空表示不修改当前用户名";
  form.password.placeholder = "留空表示不修改";
  form.pin.placeholder = "留空表示不修改";

  if (typeof filterAccountDevices === "function") filterAccountDevices(bank.value);
  const device = document.getElementById("account-device");
  if (device) device.value = button.dataset.deviceId || "";
  const collectMode = document.getElementById("account-collect-mode");
  if (collectMode) collectMode.value = button.dataset.collectMode || "both";
  renderDeviceHint();
  setAccountSchedule(button.dataset.cron || "0 8,18 * * *");
  openModal("account-modal", "#account-name-input");
}

async function submitAccountForm(event) {
  event.preventDefault();
  const form = event.target;
  const accountId = form.account_id.value;
  const payload = {
    account_name: form.account_name.value,
    collect_cron: form.collect_cron.value,
    collect_mode: form.collect_mode.value,
    days_back: parseInt(form.days_back.value || "30", 10),
    ukey_device_id: form.ukey_device_id.value ? parseInt(form.ukey_device_id.value, 10) : null,
  };
  const submit = document.getElementById("account-submit");
  if (submit) submit.disabled = true;
  try {
    if (accountId) {
      if (form.username.value.trim()) payload.username = form.username.value;
      if (form.password.value) payload.password = form.password.value;
      if (form.pin.value) payload.pin = form.pin.value;
      await patchJSON(`/api/accounts/${accountId}`, payload);
      toast("账户已更新");
    } else {
      payload.bank_code = form.bank_code.value;
      payload.account_no = form.account_no.value;
      payload.collect_enabled = true;
      payload.username = form.username.value;
      payload.password = form.password.value;
      payload.pin = form.pin.value;
      await postJSON("/api/accounts", payload);
      toast("账户已创建");
    }
    setTimeout(() => location.reload(), 600);
  } catch (error) {
    toast((accountId ? "更新失败: " : "创建失败: ") + error.message);
    if (submit) submit.disabled = false;
  }
}

async function submitDeviceForm(event) {
  event.preventDefault();
  const form = event.target;
  try {
    await postJSON("/api/devices", {
      name: form.name.value,
      bank_code: form.bank_code.value,
      backend: form.backend.value,
      agent_code: form.agent_code ? form.agent_code.value : "local",
      gf3_cabinet: parseInt(form.gf3_cabinet.value || "1", 10),
      gf3_port: parseInt(form.gf3_port.value || "1", 10),
      ter_id: form.ter_id ? form.ter_id.value : "",
      mb_user: form.mb_user ? form.mb_user.value : "",
    });
    toast("设备已创建");
    setTimeout(() => location.reload(), 600);
  } catch (error) {
    toast("创建失败: " + error.message);
  }
}

async function syncRobotDevices() {
  try {
    const result = await postJSON("/api/devices/sync");
    const source = result.source === "local" ? "本地 MB" : "服务商接口";
    const suffix = result.errors && result.errors.length ? `，${result.errors.length} 个机柜读取失败` : "";
    toast(`同步完成（${source}）: 共 ${result.total} 台，新增 ${result.created}，更新 ${result.updated}${suffix}`);
    setTimeout(() => location.reload(), 1000);
  } catch (error) {
    toast("同步失败: " + error.message);
  }
}

function filterAccountDevices(bank) {
  const select = document.getElementById("account-device");
  if (!select) return;
  let visible = 0;
  for (const option of select.options) {
    if (!option.value) {
      option.hidden = false;
      continue;
    }
    const match = !bank || option.dataset.bank === bank;
    option.hidden = !match;
    if (match) visible += 1;
  }
  const current = select.selectedOptions[0];
  if (current && current.hidden) select.value = "";
  renderDeviceHint();
  const hint = document.getElementById("device-hint");
  if (bank && visible === 0 && hint) {
    hint.textContent = "该银行暂未同步到 UKey 设备，可先选「不绑定」";
  }
  matchAccountDeviceByPort();
}

function renderDeviceHint() {
  const select = document.getElementById("account-device");
  const hint = document.getElementById("device-hint");
  if (!select || !hint) return;
  const option = select.selectedOptions[0];
  if (!option || !option.value) {
    hint.textContent = "";
    hint.style.color = "";
    return;
  }
  const cabinet = document.getElementById("account-cabinet");
  const port = document.getElementById("account-port");
  if (cabinet && option.dataset.cabinet) cabinet.value = option.dataset.cabinet;
  if (port && option.dataset.portnum) port.value = option.dataset.portnum;

  const parts = [
    `${option.dataset.backend} 机柜/端口 ${option.dataset.location}`,
    `执行机 ${option.dataset.agent}`,
    `状态 ${option.dataset.status}`,
  ];
  if (option.dataset.terid) parts.push(`终端 ${option.dataset.terid}`);
  if (option.dataset.pid) parts.push(`PID ${option.dataset.pid}`);
  if (option.dataset.cert) parts.push(`证书到期 ${option.dataset.cert}`);
  if (option.dataset.bound) parts.push(`已被账户 ${option.dataset.bound} 占用`);
  hint.textContent = parts.join(" · ");
  hint.style.color = option.dataset.bound ? "#b45309" : "";
}

function matchAccountDeviceByPort() {
  const select = document.getElementById("account-device");
  const hint = document.getElementById("device-hint");
  const cabinetInput = document.getElementById("account-cabinet");
  const portInput = document.getElementById("account-port");
  if (!select || !portInput) return;
  const port = portInput.value.trim();
  if (!port) return;
  const cabinet = cabinetInput ? cabinetInput.value.trim() : "";
  const candidates = Array.from(select.options).filter((option) => option.value && !option.hidden);
  const match = candidates.find(
    (option) => option.dataset.portnum === port && (!cabinet || option.dataset.cabinet === cabinet)
  );
  if (match) {
    if (select.value !== match.value) select.value = match.value;
    renderDeviceHint();
    return;
  }
  if (hint) {
    hint.textContent = `未找到机柜 ${cabinet || "*"} / 端口 ${port} 的可用设备`;
    hint.style.color = "#b45309";
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const bank = document.getElementById("account-bank");
  if (bank) filterAccountDevices(bank.value);
});

function initTaskAutoRefresh() {
  const statsBox = document.getElementById("task-stats");
  const tableBox = document.getElementById("task-table");
  if (!statsBox || !tableBox) return;

  const params = new URLSearchParams(window.location.search);
  const status = params.get("status") || "";
  const page = params.get("page") || "1";
  const source = params.get("source") || "";
  const activeCount = () => Number(document.getElementById("task-stats")?.dataset.active || 0);
  let timer = null;
  let refreshing = false;

  const schedule = (delay) => {
    if (timer) window.clearTimeout(timer);
    timer = delay > 0 ? window.setTimeout(refresh, delay) : null;
  };

  const refresh = async () => {
    if (refreshing || document.hidden) return;
    if (document.querySelector(".modal.open")) {
      schedule(activeCount() > 0 ? 5000 : 0);
      return;
    }
    refreshing = true;
    const scrollY = window.scrollY;
    try {
      const query = new URLSearchParams();
      if (status) query.set("status", status);
      if (page && page !== "1") query.set("page", page);
      if (source) query.set("source", source);
      const suffix = query.toString() ? `?${query.toString()}` : "";
      const response = await fetch(`/tasks/partial${suffix}`, {
        cache: "no-store",
        credentials: "same-origin",
      });
      if (response.ok) {
        const doc = new DOMParser().parseFromString(await response.text(), "text/html");
        for (const id of ["task-total-chip", "task-stats", "task-table"]) {
          const next = doc.getElementById(id);
          const current = document.getElementById(id);
          if (next && current) current.replaceWith(next);
        }
        if (window.scrollY !== scrollY) window.scrollTo(0, scrollY);
      }
    } catch (error) {
      // 网络抖动时保持当前内容
    } finally {
      refreshing = false;
      schedule(activeCount() > 0 ? 3000 : 0);
    }
  };

  schedule(activeCount() > 0 ? 3000 : 0);
  const wake = () => {
    if (document.hidden) return;
    if (timer) window.clearTimeout(timer);
    refresh();
  };
  document.addEventListener("visibilitychange", wake);
  window.addEventListener("focus", wake);
}

document.addEventListener("DOMContentLoaded", initTaskAutoRefresh);


async function createReconBatch(event) {
  event.preventDefault();
  const form = event.target;
  try {
    const result = await postJSON("/api/recon/batches", {
      period_start: form.period_start.value,
      period_end: form.period_end.value,
      name: form.name.value,
    });
    toast(`对账完成: 匹配 ${result.matched}，未匹配 ${result.unmatched}`);
    location.href = `/recon?batch_id=${result.id}`;
  } catch (error) {
    toast("对账失败: " + error.message);
  }
}

async function importDocs(event) {
  event.preventDefault();
  const form = event.target;
  const data = new FormData();
  data.append("file", form.file.files[0]);
  try {
    const response = await fetch("/api/recon/import", { method: "POST", body: data });
    const result = await response.json();
    toast(`导入完成: 成功 ${result.inserted}，跳过 ${result.skipped}`);
    setTimeout(() => location.reload(), 900);
  } catch (error) {
    toast("导入失败: " + error.message);
  }
}

function setupClientPager(container) {
  if (container.dataset.pagerReady === "1") return;
  container.dataset.pagerReady = "1";

  const size = Math.max(1, parseInt(container.dataset.pageSize || "10", 10));
  const selector = container.dataset.pageItems || ":scope > *";
  const unit = container.dataset.pageUnit || "条";
  const pager = document.createElement("div");
  pager.className = "pagination client-pagination";
  container.insertAdjacentElement("afterend", pager);
  let page = 1;

  const items = () =>
    Array.from(container.querySelectorAll(selector)).filter((item) => !item.classList.contains("hidden"));

  const addButton = (label, target, { active = false, disabled = false } = {}) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "page-btn" + (active ? " active" : "") + (disabled ? " disabled" : "");
    button.textContent = label;
    button.disabled = disabled;
    if (active) button.setAttribute("aria-current", "page");
    button.addEventListener("click", () => {
      page = target;
      render();
    });
    pager.appendChild(button);
  };

  const render = () => {
    const rows = items();
    const pages = Math.max(1, Math.ceil(rows.length / size));
    page = Math.min(Math.max(1, page), pages);
    rows.forEach((item, index) => {
      item.classList.toggle("page-hidden", index < (page - 1) * size || index >= page * size);
    });

    pager.replaceChildren();
    if (rows.length <= size) {
      pager.classList.add("page-hidden");
      rows.forEach((item) => item.classList.remove("page-hidden"));
      return;
    }
    pager.classList.remove("page-hidden");

    const info = document.createElement("span");
    info.className = "page-info client-page-info";
    info.textContent = `共 ${rows.length} ${unit} · 第 ${page} / ${pages} 页`;
    pager.appendChild(info);

    addButton("‹", page - 1, { disabled: page <= 1 });
    const numbers = typeof logPageNumbers === "function" ? logPageNumbers(page, pages) : [1];
    numbers.forEach((number) => {
      if (number === "…") {
        const gap = document.createElement("span");
        gap.className = "page-gap";
        gap.textContent = "…";
        pager.appendChild(gap);
      } else {
        addButton(String(number), number, { active: number === page });
      }
    });
    addButton("›", page + 1, { disabled: page >= pages });
  };

  container._pagerRefresh = (reset = false) => {
    if (reset) page = 1;
    render();
  };
  render();
}

function initClientPagers() {
  document.querySelectorAll("[data-page-size]").forEach(setupClientPager);
}

function refreshPagers(reset = false) {
  document.querySelectorAll("[data-page-size]").forEach((container) => {
    if (typeof container._pagerRefresh === "function") container._pagerRefresh(reset);
  });
}

document.addEventListener("DOMContentLoaded", initClientPagers);
