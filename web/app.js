/* 前端交互：调用 window.pywebview.api.*，处理状态、禁用与提示 */

const $ = (id) => document.getElementById(id);
const state = { mode: "text", scripts: [], selected: null, busy: false };

/* 等待 pywebview 桥接就绪 */
function apiReady() {
  return new Promise((resolve) => {
    if (window.pywebview && window.pywebview.api) return resolve();
    window.addEventListener("pywebviewready", () => resolve(), { once: true });
  });
}

/* ---------- 通用 UI 辅助 ---------- */
function toast(msg, kind = "info", ms = 2600) {
  const t = $("toast");
  t.textContent = msg;
  t.className = `toast show ${kind}`;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.className = "toast hidden"), ms);
}

function setStatus(el, text, cls = "") {
  const s = $(el);
  s.textContent = text;
  s.className = "step-status " + cls;
}

/* 运行期间：标记按钮 busy 并整体锁定，杜绝重复点击 */
async function withBusy(btnId, statusId, label, fn) {
  if (state.busy) return;
  state.busy = true;
  const btn = $(btnId);
  btn.classList.add("busy");
  btn.disabled = true;
  setStatus(statusId, label, "run");
  lockSteps(true);
  try {
    return await fn();
  } finally {
    btn.classList.remove("busy");
    state.busy = false;
    lockSteps(false);
  }
}

function lockSteps(lock) {
  ["step1", "importBtn", "fetchBtn"].forEach((id) => ($(id).disabled = lock));
  // 步骤 2/3 的可用性由各自前置条件决定
  $("step2").disabled = lock || !window.__storyReady;
  $("step3").disabled = lock || state.selected == null;
}

/* ---------- 状态 / 设置 ---------- */
async function refreshStatus() {
  const st = await window.pywebview.api.get_status();
  const pill = $("statusPill");
  if (st.api_connected) {
    pill.className = "pill online";
    $("statusText").textContent = "已连接模型";
  } else {
    pill.className = "pill offline";
    $("statusText").textContent = "本地规则模式";
  }
  // 回填设置表单
  $("cfgUrl").value = st.config.api_url || "";
  $("cfgModel").value = st.config.model || "";
  $("cfgTemp").value = st.config.temperature ?? 0.7;
  $("cfgKey").placeholder = st.config.has_key ? "已保存（留空则不修改）" : "粘贴你的 API Key";
}

function openSettings() { $("settingsModal").classList.remove("hidden"); }
function closeSettings() { $("settingsModal").classList.add("hidden"); }

async function saveSettings() {
  const cfg = {
    api_url: $("cfgUrl").value,
    api_key: $("cfgKey").value,
    model: $("cfgModel").value,
    temperature: $("cfgTemp").value,
  };
  const res = await window.pywebview.api.save_settings(cfg);
  if (res.ok) {
    closeSettings();
    $("cfgKey").value = "";
    await refreshStatus();
    toast(res.api_connected ? "已连接模型 ✓" : "已保存（当前为本地模式）", res.api_connected ? "ok" : "info");
  } else {
    toast(res.error || "保存失败", "err");
  }
}

/* ---------- 输入区 ---------- */
function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
  $("fetchBtn").classList.toggle("hidden", mode !== "url");
  $("input").placeholder = mode === "url"
    ? "输入网址，例如 https://example.com/article"
    : "在这里粘贴长文本/小说，或切换到「网址」模式输入链接…";
}

function updateCharCount() {
  $("charCount").textContent = `${$("input").value.length} 字`;
}

async function importTxt() {
  const res = await window.pywebview.api.import_txt();
  if (res.ok) {
    setMode("text");
    $("input").value = res.text;
    updateCharCount();
    toast("已导入文本", "ok");
  } else if (!res.cancelled) {
    toast(res.error || "导入失败", "err");
  }
}

async function fetchPreview() {
  await withBusy("fetchBtn", "status1", "抓取中…", async () => {
    const res = await window.pywebview.api.fetch_preview($("input").value);
    if (res.ok) {
      setMode("text");
      $("input").value = res.text;
      updateCharCount();
      setStatus("status1", "已抓取，请确认后编排", "ok");
      toast("已抓取正文，可编辑后再编排故事", "ok");
    } else {
      setStatus("status1", "抓取失败", "err");
      toast(res.error || "抓取失败", "err");
    }
  });
}

/* ---------- 步骤 1 ---------- */
async function arrange() {
  await withBusy("step1", "status1", "编排中…", async () => {
    const res = await window.pywebview.api.arrange($("input").value, state.mode);
    if (!res.ok) {
      setStatus("status1", "失败", "err");
      toast(res.error || "编排失败", "err");
      return;
    }
    renderStory(res.story);
    $("projectPath").textContent = "项目：" + res.project_dir;
    $("openFolderBtn").disabled = false;
    window.__storyReady = true;
    $("step2").disabled = false;
    // 重置后续步骤
    state.scripts = []; state.selected = null;
    renderScripts([]); $("scriptDetail").className = "result-body empty";
    $("scriptDetail").textContent = "在上方列表选择一集，这里显示旁白脚本与镜头提示词。";
    $("step3").disabled = true;
    setStatus("status2", "未开始"); setStatus("status3", "未开始");
    setStatus("status1", res.used_model ? "成功（模型）" : "成功（本地）", "ok");
    toast("故事已编排并保存", "ok");
  });
}

function renderStory(s) {
  const el = $("storyResult");
  el.className = "result-body";
  el.innerHTML =
    `<h3>标题</h3>${esc(s.title)}` +
    `<h3>故事梗概</h3>${esc(s.outline)}` +
    `<h3>主要人物 / 要素</h3>${esc(s.characters)}` +
    `<h3>视频风格</h3>${esc(s.style)}`;
}

/* ---------- 步骤 2 ---------- */
async function generate() {
  await withBusy("step2", "status2", "生成中…", async () => {
    const res = await window.pywebview.api.generate(
      Number($("seconds").value) || 60,
      Number($("count").value) || 0
    );
    if (!res.ok) {
      setStatus("status2", "失败", "err");
      toast(res.error || "生成失败", "err");
      return;
    }
    state.scripts = res.scripts;
    renderScripts(res.scripts);
    if (res.scripts.length) selectScript(res.scripts[0].index);
    setStatus("status2", `成功 · 共 ${res.scripts.length} 集` + (res.used_model ? "（模型）" : "（本地）"), "ok");
    toast(`已生成 ${res.scripts.length} 个脚本`, "ok");
  });
}

function renderScripts(list) {
  const ul = $("scriptList");
  ul.innerHTML = "";
  const badge = $("scriptCount");
  if (!list.length) {
    ul.className = "script-list empty";
    ul.innerHTML = '<li class="placeholder">完成「生成脚本」后在这里列出每一集。</li>';
    badge.classList.add("hidden");
    return;
  }
  ul.className = "script-list";
  badge.textContent = list.length;
  badge.classList.remove("hidden");
  list.forEach((s) => {
    const li = document.createElement("li");
    li.className = "script-item";
    li.dataset.index = s.index;
    li.innerHTML = `<span class="idx">${s.index}</span><span class="t">${esc(s.title)}</span><span class="shots">${s.shots.length} 镜</span>`;
    li.onclick = () => selectScript(s.index);
    ul.appendChild(li);
  });
}

function selectScript(index) {
  state.selected = index;
  document.querySelectorAll(".script-item").forEach((li) =>
    li.classList.toggle("active", Number(li.dataset.index) === index)
  );
  const s = state.scripts.find((x) => x.index === index);
  if (!s) return;
  const shots = s.shots.map((sh, i) =>
    `<div class="shot-card"><div class="shot-h">镜 ${i + 1} · ${esc(String(sh.duration))} 秒</div>` +
    `画面提示词：${esc(sh.visual_prompt)}<br>旁白：${esc(sh.voiceover)}</div>`
  ).join("");
  const el = $("scriptDetail");
  el.className = "result-body";
  el.innerHTML =
    `<h3>${esc(s.title)}</h3>` +
    `<b>摘要：</b>${esc(s.summary)}` +
    `<h3>旁白脚本</h3>${esc(s.narration)}` +
    `<h3>镜头提示词</h3>${shots}`;
  if (!state.busy) $("step3").disabled = false;
}

/* ---------- 步骤 3 ---------- */
async function runScript() {
  if (state.selected == null) { toast("请先选择一个脚本", "info"); return; }
  await withBusy("step3", "status3", "执行中…", async () => {
    const res = await window.pywebview.api.run_script(state.selected);
    if (res.ok) {
      setStatus("status3", "成功", "ok");
      toast("已生成执行记录", "ok");
    } else {
      setStatus("status3", "失败", "err");
      toast(res.error || "执行失败", "err");
    }
  });
}

async function openFolder() {
  const res = await window.pywebview.api.open_output();
  if (!res.ok) toast(res.error || "无法打开文件夹", "err");
}

/* ---------- 工具 ---------- */
function esc(s) {
  return String(s ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

/* ---------- 绑定 ---------- */
function bind() {
  document.querySelectorAll(".seg-btn").forEach((b) => (b.onclick = () => setMode(b.dataset.mode)));
  $("input").addEventListener("input", updateCharCount);
  $("importBtn").onclick = importTxt;
  $("fetchBtn").onclick = fetchPreview;
  $("step1").onclick = arrange;
  $("step2").onclick = generate;
  $("step3").onclick = runScript;
  $("openFolderBtn").onclick = openFolder;
  $("settingsBtn").onclick = openSettings;
  $("statusPill").onclick = openSettings;
  $("closeSettings").onclick = closeSettings;
  $("cancelSettings").onclick = closeSettings;
  $("saveSettings").onclick = saveSettings;
  $("settingsModal").addEventListener("click", (e) => { if (e.target.id === "settingsModal") closeSettings(); });
}

apiReady().then(async () => {
  bind();
  setMode("text");
  updateCharCount();
  await refreshStatus();
});
