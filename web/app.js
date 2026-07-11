/* 前端交互：调用 window.pywebview.api.*，处理状态、禁用与提示 */

const $ = (id) => document.getElementById(id);
const state = { mode: "text", scripts: [], selected: null, busy: false };

/* 等待 pywebview 桥接就绪。
   注意：window.pywebview.api 先被注入为空对象、方法列表随后才填充，
   所以必须确认具体方法存在才算就绪；轮询 + 事件双保险，超时则报错。 */
function apiReady(timeoutMs = 30000) {
  const ok = () =>
    window.pywebview && window.pywebview.api &&
    typeof window.pywebview.api.get_status === "function";
  return new Promise((resolve, reject) => {
    if (ok()) return resolve();
    const started = Date.now();
    const timer = setInterval(() => {
      if (ok()) { clearInterval(timer); resolve(); }
      else if (Date.now() - started > timeoutMs) {
        clearInterval(timer);
        reject(new Error("pywebview 桥接超时"));
      }
    }, 250);
    window.addEventListener("pywebviewready", () => {
      if (ok()) { clearInterval(timer); resolve(); }
    }, { once: true });
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
  ["step1", "importBtn", "fetchBtn", "parseScriptBtn"].forEach((id) => {
    const el = $(id);
    if (el) el.disabled = lock;
  });
  // 步骤 2/3 的可用性由各自前置条件决定
  $("step2").disabled = lock || !window.__storyReady;
  $("step3").disabled = lock || state.selected == null;
  $("stepAll").disabled = lock || !(state.scripts && state.scripts.length);
  $("previewRefsBtn").disabled = lock || !window.__storyReady;
  $("editScriptBtn").disabled = lock || state.selected == null;
  $("redoBtn").disabled = lock || state.selected == null;
}

/* ---------- 状态 / 设置 ---------- */
async function refreshStatus() {
  const st = await window.pywebview.api.get_status();
  if (st.theme) applyTheme(st.theme);
  const pill = $("statusPill");
  if (st.api_connected) {
    pill.className = "pill online";
    $("statusText").textContent = "已连接模型";
  } else {
    pill.className = "pill offline";
    $("statusText").textContent = "本地规则模式";
  }
  // 回填设置表单
  const c = st.config;
  $("cfgUrl").value = c.api_url || "";
  $("cfgModel").value = c.model || "";
  $("cfgTemp").value = c.temperature ?? 0.7;
  $("cfgKey").placeholder = c.has_key ? "已保存（留空则不修改）" : "粘贴你的 API Key";
  $("cfgXaiKey").placeholder = c.has_xai ? "已保存（留空则不修改）" : "粘贴 xAI API Key";
  $("cfgGeminiKey").placeholder = c.has_gemini ? "已保存（留空则不修改）" : "粘贴 Gemini API Key";
  $("cfgVoice").value = c.gemini_voice || "Kore";
  $("cfgAspect").value = c.video_aspect_ratio || "9:16";
  $("cfgRes").value = c.video_resolution || "720p";
  $("cfgConsistency").value = c.consistency || "strong";
}

/* ---------- 配色主题 ---------- */
const THEMES = ["orange", "red", "blue", "green", "purple", "pink", "yellow"];

function applyTheme(name) {
  if (!THEMES.includes(name)) name = "orange";
  document.body.dataset.theme = name;
  document.querySelectorAll(".swatch").forEach((s) =>
    s.classList.toggle("active", s.dataset.theme === name)
  );
}

function toggleThemePop() { $("themePop").classList.toggle("hidden"); }

async function chooseTheme(name) {
  applyTheme(name);
  $("themePop").classList.add("hidden");
  try { await window.pywebview.api.set_theme(name); } catch (e) {}
}

function openSettings() { $("settingsModal").classList.remove("hidden"); }
function closeSettings() { $("settingsModal").classList.add("hidden"); }

async function saveSettings() {
  const cfg = {
    api_url: $("cfgUrl").value,
    api_key: $("cfgKey").value,
    model: $("cfgModel").value,
    temperature: $("cfgTemp").value,
    xai_api_key: $("cfgXaiKey").value,
    gemini_api_key: $("cfgGeminiKey").value,
    gemini_voice: $("cfgVoice").value,
    video_aspect_ratio: $("cfgAspect").value,
    video_resolution: $("cfgRes").value,
    consistency: $("cfgConsistency").value,
  };
  const res = await window.pywebview.api.save_settings(cfg);
  if (res.ok) {
    closeSettings();
    ["cfgKey", "cfgXaiKey", "cfgGeminiKey"].forEach((id) => ($(id).value = ""));
    await refreshStatus();
    toast(res.api_connected ? "已保存，文本模型已连接 ✓" : "已保存", res.api_connected ? "ok" : "info");
  } else {
    toast(res.error || "保存失败", "err");
  }
}

/* ---------- 输入区 ---------- */
function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
  const fetchBtn = $("fetchBtn");
  if (fetchBtn) fetchBtn.classList.toggle("hidden", mode !== "url");
  const parseBtn = $("parseScriptBtn");
  if (parseBtn) parseBtn.classList.toggle("hidden", mode !== "script");
  const importBtn = $("importBtn");
  if (importBtn) importBtn.classList.toggle("hidden", mode === "url" || mode === "script");
  
  const inputEl = $("input");
  if (inputEl) {
    if (mode === "url") {
      inputEl.placeholder = "输入网址，例如 https://example.com/article";
    } else if (mode === "script") {
      inputEl.placeholder = "在此粘贴由 AI 产生的多集 JSON 脚本（集数数组，包含 index, title, summary, scenes 等字段）…";
    } else {
      inputEl.placeholder = "在这里粘贴长文本/小说，或切换到「网址」模式输入链接…";
    }
  }
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

async function parseScriptInput() {
  const rawInput = $("input").value.trim();
  if (!rawInput) {
    toast("请先粘贴脚本提示词 JSON", "info");
    return;
  }
  await withBusy("parseScriptBtn", "status2", "解析脚本中…", async () => {
    const res = await window.pywebview.api.import_parsed_scripts(rawInput);
    if (!res.ok) {
      setStatus("status2", "解析失败", "err");
      toast(res.error || "解析脚本失败，请检查 JSON 格式", "err");
      return;
    }
    state.story = res.story;
    state.scripts = res.scripts;
    renderStory(res.story);
    renderScripts(res.scripts);
    if (res.scripts.length) {
      selectScript(res.scripts[0].index);
    }
    $("projectPath").textContent = "项目：" + res.project_dir;
    $("openFolderBtn").disabled = false;
    window.__storyReady = true;
    $("step2").disabled = false;
    $("stepAll").disabled = false;
    setStatus("status1", "导入脚本成功", "ok");
    setStatus("status2", `成功 · 共 ${res.scripts.length} 集`, "ok");
    setStatus("status3", "未开始");
    await showEstimate();
    await refreshJobs();
    toast(`成功导入并解析 ${res.scripts.length} 个脚本`, "ok");
  });
}

/* ---------- Step 1 ---------- */
async function arrange() {
  if (state.mode === "script") {
    toast("当前是「粘贴脚本提示词」模式，请点击「脚本分类」按钮导入", "info");
    return;
  }
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
  let castHtml = "";
  if (s.cast && s.cast.length) {
    castHtml = "<h3>角色表</h3>" + s.cast.map((c) =>
      `<b>${esc(c.name)}</b>：${esc(c.appearance || "")}` + (c.persona ? `（${esc(c.persona)}）` : "")
    ).join("<br>");
  } else {
    castHtml = `<h3>主要人物 / 要素</h3>${esc(s.characters)}`;
  }
  let locHtml = "";
  if (s.locations && s.locations.length) {
    locHtml = "<h3>场景表</h3>" + s.locations.map((l) =>
      `<b>${esc(l.name)}</b>：${esc(l.description || "")}`).join("<br>");
  }
  el.innerHTML =
    `<h3>标题</h3>${esc(s.title)}` +
    `<h3>故事梗概</h3>${esc(s.outline)}` +
    castHtml + locHtml +
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
    $("stepAll").disabled = false;
    await showEstimate();
    await refreshJobs();
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
  let body;
  if (s.scenes && s.scenes.length) {
    body = s.scenes.map((sc, si) => {
      const head = `场景 ${si + 1}` + (sc.location ? `｜${esc(sc.location)}` : "");
      const beats = (sc.beats || []).map((b) =>
        `<div class="beat"><span class="spk">${esc(b.speaker || "旁白")}</span>` +
        `<span class="line">${esc(b.line || "")}</span>` +
        (b.action ? `<span class="act">（${esc(b.action)}）</span>` : "") +
        `<div class="shotp">画面：${esc(b.shot_prompt || "")} · ${esc(String(b.duration || ""))}s</div></div>`
      ).join("");
      return `<div class="scene"><div class="shot-h"><span>${head}</span>` +
        `<button class="copy-scene-btn" data-si="${si}" title="复制本场景的旁白/台词/动作/画面提示词">复制</button></div>${beats}</div>`;
    }).join("");
  } else {
    body = s.shots.map((sh, i) =>
      `<div class="shot-card"><div class="shot-h">镜 ${i + 1} · ${esc(String(sh.duration))} 秒</div>` +
      `画面提示词：${esc(sh.visual_prompt)}<br>旁白：${esc(sh.voiceover)}</div>`
    ).join("");
  }
  const el = $("scriptDetail");
  el.className = "result-body";
  el.innerHTML = `<h3>${esc(s.title)}</h3><b>摘要：</b>${esc(s.summary)}<h3>剧本</h3>${body}`;
  el.querySelectorAll(".copy-scene-btn").forEach((btn) => {
    btn.onclick = () => copyScenePrompt(s, Number(btn.dataset.si));
  });
  if (!state.busy) lockSteps(false);
}

/* 把单个场景拼成可直接投喂视频大模型的提示词并复制 */
function sceneToPrompt(s, sc, si) {
  const lines = [`《${s.title || ""}》场景 ${si + 1}` + (sc.location ? `｜${sc.location}` : "")];
  (sc.beats || []).forEach((b) => {
    let t = `${b.speaker || "旁白"}：${b.line || ""}`;
    if (b.action) t += `（${b.action}）`;
    lines.push(t);
    if (b.shot_prompt) {
      lines.push(`画面：${b.shot_prompt}` + (b.duration ? ` · ${b.duration}s` : ""));
    }
  });
  return lines.join("\n");
}

async function copyScenePrompt(s, si) {
  const sc = (s.scenes || [])[si];
  if (!sc) return;
  const ok = await copyText(sceneToPrompt(s, sc, si));
  toast(ok ? `已复制场景 ${si + 1} 提示词 ✓` : "复制失败，请手动选择文本复制", ok ? "ok" : "err");
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (e) {
    /* file:// 等非安全上下文下 clipboard API 不可用，退回 execCommand */
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;top:0;left:0;opacity:0;";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (_) {}
    ta.remove();
    return ok;
  }
}

/* ---------- 步骤 3 ---------- */
/* 制作进度：由后端通过 evaluate_js 调用 */
window.onProduceProgress = function (msg) {
  const log = $("produceLog");
  log.classList.remove("hidden");
  const line = document.createElement("div");
  line.textContent = "• " + msg;
  log.appendChild(line);
  log.scrollTop = log.scrollHeight;
};

async function runScript() {
  if (state.selected == null) { toast("请先选择一个脚本", "info"); return; }
  const log = $("produceLog");
  log.innerHTML = "";
  log.classList.remove("hidden");
  await withBusy("step3", "status3", "制作视频中…", async () => {
    const res = await window.pywebview.api.run_script(state.selected);
    if (res.ok) {
      setStatus("status3", "成功", "ok");
      window.onProduceProgress("完成：" + res.video_path);
      await refreshJobs();
      toast("视频已生成 ✓", "ok");
    } else {
      setStatus("status3", "失败", "err");
      window.onProduceProgress("失败：" + (res.error || "未知错误"));
      toast(res.error || "制作失败", "err");
    }
  });
}

async function showEstimate() {
  const res = await window.pywebview.api.estimate();
  const bar = $("estimateBar");
  if (res.ok) {
    bar.classList.remove("hidden");
    bar.textContent =
      `预估：${res.episodes} 集 · ${res.shots} 个镜头 · ` +
      `视频生成 ${res.video_calls} 次 / 参考图 ${res.image_calls} 次 / 配音 ${res.tts_calls} 次 · ` +
      `粗略耗时约 ${res.est_minutes} 分钟（真实视模型排队而定）`;
  } else {
    bar.classList.add("hidden");
  }
}

async function refreshJobs() {
  const res = await window.pywebview.api.get_jobs();
  const eps = (res && res.episodes) || {};
  document.querySelectorAll(".script-item").forEach((li) => {
    const st = (eps[li.dataset.index] || {}).status;
    let badge = li.querySelector(".jobst");
    if (!badge) {
      badge = document.createElement("span");
      badge.className = "jobst";
      li.appendChild(badge);
    }
    const map = { done: ["✓", "done"], failed: ["✗", "failed"], running: ["…", "running"] };
    const m = map[st];
    badge.textContent = m ? m[0] : "";
    badge.className = "jobst" + (m ? " " + m[1] : "");
  });
}

async function runAll() {
  const log = $("produceLog");
  log.innerHTML = "";
  log.classList.remove("hidden");
  await withBusy("stepAll", "statusAll", "批量制作中…", async () => {
    const res = await window.pywebview.api.run_all();
    await refreshJobs();
    if (res.ok) {
      setStatus("statusAll", `完成 ${res.done} · 跳过 ${res.skipped} · 失败 ${res.failed}`,
        res.failed ? "err" : "ok");
      toast(`批量完成：成功 ${res.done}，失败 ${res.failed}`, res.failed ? "info" : "ok");
    } else {
      setStatus("statusAll", "失败", "err");
      toast(res.error || "批量制作失败", "err");
    }
  });
}

async function openFolder() {
  const res = await window.pywebview.api.open_output();
  if (!res.ok) toast(res.error || "无法打开文件夹", "err");
}

/* ---------- 第5期：预览 / 编辑 / 重做 ---------- */
async function previewRefs() {
  if (state.busy) return;
  await withBusy("previewRefsBtn", "status2", "生成参考图…", async () => {
    const res = await window.pywebview.api.preview_refs();
    const panel = $("refsPanel");
    if (!res.ok) { toast(res.error || "预览失败", "err"); return; }
    if (!res.refs.length) { toast("当前没有角色/场景可预览（需模型模式产出角色表）", "info"); return; }
    panel.classList.remove("hidden");
    panel.innerHTML = res.refs.map((r) =>
      `<figure><img src="${r.data}" alt="${esc(r.name)}"/><figcaption>${esc(r.type)}·${esc(r.name)}</figcaption></figure>`
    ).join("");
    setStatus("status2", "参考图已就绪", "ok");
  });
}

function toggleEditor() {
  const s = state.scripts.find((x) => x.index === state.selected);
  if (!s) { toast("请先选择一集", "info"); return; }
  const editor = $("scriptEditor");
  if (!editor.classList.contains("hidden")) { editor.classList.add("hidden"); $("scriptDetail").classList.remove("hidden"); return; }
  const scenes = (s.scenes && s.scenes.length) ? s.scenes : [{ location: "", beats: s.shots.map((sh) => ({ speaker: sh.speaker || "旁白", line: sh.voiceover || "", action: sh.action || "", shot_prompt: sh.visual_prompt || "", duration: sh.duration || 0 })) }];
  let html = `<label class="field">标题<input id="edTitle" type="text" value="${esc(s.title)}"/></label>`;
  scenes.forEach((sc, si) => {
    html += `<div class="ed-scene" data-si="${si}"><input class="ed-loc" placeholder="场景地点" value="${esc(sc.location || "")}"/>`;
    (sc.beats || []).forEach((b, bi) => {
      html += `<div class="ed-beat" data-bi="${bi}">` +
        `<input class="ed-spk" placeholder="说话人" value="${esc(b.speaker || "")}"/>` +
        `<input class="ed-line" placeholder="台词" value="${esc(b.line || "")}"/>` +
        `<input class="ed-act" placeholder="动作(可选)" value="${esc(b.action || "")}"/></div>`;
    });
    html += `</div>`;
  });
  html += `<div class="modal-foot"><button id="edCancel" class="ghost-btn">取消</button><button id="edSave" class="primary-btn">保存修改</button></div>`;
  editor.innerHTML = html;
  editor.classList.remove("hidden");
  $("scriptDetail").classList.add("hidden");
  $("edCancel").onclick = toggleEditor;
  $("edSave").onclick = saveScriptEdit;
}

async function saveScriptEdit() {
  const s = state.scripts.find((x) => x.index === state.selected);
  if (!s) return;
  const scenes = [...document.querySelectorAll(".ed-scene")].map((sc) => ({
    location: sc.querySelector(".ed-loc").value,
    beats: [...sc.querySelectorAll(".ed-beat")].map((b) => ({
      speaker: b.querySelector(".ed-spk").value,
      line: b.querySelector(".ed-line").value,
      action: b.querySelector(".ed-act").value,
    })),
  }));
  const res = await window.pywebview.api.update_script(state.selected, { title: $("edTitle").value, scenes });
  if (!res.ok) { toast(res.error || "保存失败", "err"); return; }
  const i = state.scripts.findIndex((x) => x.index === state.selected);
  state.scripts[i] = res.script;
  renderScripts(state.scripts);
  selectScript(state.selected);
  $("scriptEditor").classList.add("hidden");
  $("scriptDetail").classList.remove("hidden");
  await refreshJobs();
  toast("已保存，该集将重做", "ok");
}

async function redoEpisode() {
  if (state.selected == null) { toast("请先选择一集", "info"); return; }
  const log = $("produceLog"); log.innerHTML = ""; log.classList.remove("hidden");
  await withBusy("redoBtn", "status3", "重做本集…", async () => {
    const res = await window.pywebview.api.run_script(state.selected, true);
    await refreshJobs();
    if (res.ok) { setStatus("status3", "已重做", "ok"); toast("本集已重做 ✓", "ok"); }
    else { setStatus("status3", "失败", "err"); toast(res.error || "重做失败", "err"); }
  });
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
  const parseBtn = $("parseScriptBtn");
  if (parseBtn) parseBtn.onclick = parseScriptInput;
  $("step1").onclick = arrange;
  $("step2").onclick = generate;
  $("step3").onclick = runScript;
  $("stepAll").onclick = runAll;
  $("previewRefsBtn").onclick = previewRefs;
  $("editScriptBtn").onclick = toggleEditor;
  $("redoBtn").onclick = redoEpisode;
  $("openFolderBtn").onclick = openFolder;
  $("settingsBtn").onclick = openSettings;
  $("statusPill").onclick = openSettings;
  $("themeBtn").onclick = (e) => { e.stopPropagation(); toggleThemePop(); };
  document.querySelectorAll(".swatch").forEach((s) => (s.onclick = () => chooseTheme(s.dataset.theme)));
  document.addEventListener("click", (e) => {
    if (!$("themePop").contains(e.target) && e.target.id !== "themeBtn") $("themePop").classList.add("hidden");
  });
  $("closeSettings").onclick = closeSettings;
  $("cancelSettings").onclick = closeSettings;
  $("saveSettings").onclick = saveSettings;
  $("settingsModal").addEventListener("click", (e) => { if (e.target.id === "settingsModal") closeSettings(); });
}

async function boot() {
  /* 界面绑定不依赖桥接，先做，保证按钮/主题等始终可交互 */
  bind();
  setMode("text");
  updateCharCount();
  try {
    await apiReady();
    await refreshStatus();
  } catch (e) {
    /* 桥接失败或 get_status 出错：明确告知，而不是永远停在「检测中…」 */
    $("statusPill").className = "pill offline";
    $("statusText").textContent = "连接后台失败";
    toast("界面与程序后台的桥接未就绪，请关闭后重新打开；若反复出现，请重装 WebView2 运行时", "err", 9000);
  }
}

boot();
