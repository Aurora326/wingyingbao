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

async function collectNow(accountId) {
  try {
    await postJSON(`/api/accounts/${accountId}/collect`);
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

async function showLog(taskId) {
  const box = document.getElementById("log-box");
  const title = document.getElementById("log-title");
  if (title) title.textContent = `任务 #${taskId} 执行日志`;
  if (box) box.textContent = "加载中...";
  document.getElementById("log-modal")?.classList.add("open");
  try {
    const data = await getJSON(`/api/tasks/${taskId}/log`);
    const runs = data.items || [];
    if (!runs.length) {
      box.textContent = "暂无执行记录";
      return;
    }
    box.textContent = runs
      .map((run) => `--- 第 ${run.attempt} 次 (exit=${run.exit_code}, ${run.duration_ms}ms) ---\n${run.log || ""}`)
      .join("\n\n");
  } catch (error) {
    box.textContent = "读取失败: " + error.message;
  }
}

function closeLog() {
  document.getElementById("log-modal")?.classList.remove("open");
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

async function submitAccountForm(event) {
  event.preventDefault();
  const form = event.target;
  const payload = {
    bank_code: form.bank_code.value,
    account_no: form.account_no.value,
    account_name: form.account_name.value,
    collect_cron: form.collect_cron.value,
    days_back: parseInt(form.days_back.value || "30", 10),
    collect_enabled: true,
    ukey_device_id: form.ukey_device_id.value ? parseInt(form.ukey_device_id.value, 10) : null,
    username: form.username.value,
    password: form.password.value,
    pin: form.pin.value,
  };
  try {
    await postJSON("/api/accounts", payload);
    toast("账户已创建");
    setTimeout(() => location.reload(), 600);
  } catch (error) {
    toast("创建失败: " + error.message);
  }
}

async function submitCredentialForm(event) {
  event.preventDefault();
  const form = event.target;
  const accountId = form.account_id.value;
  try {
    await postJSON(`/api/accounts/${accountId}/credential`, {
      username: form.username.value,
      password: form.password.value,
      pin: form.pin.value,
    });
    toast("凭据已更新");
  } catch (error) {
    toast("更新失败: " + error.message);
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

  const status = new URLSearchParams(window.location.search).get("status") || "";
  let refreshing = false;

  const refresh = async () => {
    if (refreshing || document.hidden) return;
    if (document.querySelector(".modal.open")) return;
    refreshing = true;
    const scrollY = window.scrollY;
    try {
      const query = status ? `?status=${encodeURIComponent(status)}` : "";
      const response = await fetch(`/tasks/partial${query}`, {
        cache: "no-store",
        credentials: "same-origin",
      });
      if (!response.ok) return;
      const doc = new DOMParser().parseFromString(await response.text(), "text/html");
      for (const id of ["task-total-chip", "task-stats", "task-table"]) {
        const next = doc.getElementById(id);
        const current = document.getElementById(id);
        if (next && current) current.replaceWith(next);
      }
      if (window.scrollY !== scrollY) window.scrollTo(0, scrollY);
    } catch (error) {
      // 网络抖动时保持当前内容，下一轮继续
    } finally {
      refreshing = false;
    }
  };

  window.setInterval(refresh, 5000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh();
  });
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
