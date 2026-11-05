"use strict";
const $ = (id) => document.getElementById(id);
const state = { file: null, objectURL: null, job: null, jobs: [], models: [], tab: "timeline", view: "studio", dirty: false, uploading: false, pollBusy: false, renderedSegments: "", toastTimer: null };
const activeStatuses = new Set(["queued", "downloading", "preparing", "transcribing"]);
const speedLabels = {fast:"快",medium:"中",slow:"慢"};
const labels = { queued: "排队中", uploading: "导入中", downloading: "下载模型", preparing: "准备中", transcribing: "转写中", completed: "已完成", failed: "失败", cancelled: "已取消" };
const escapeHTML = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const icon = (name) => `<svg class="icon" aria-hidden="true"><use href="/static/icons.svg#${name}"/></svg>`;
const formatSize = (size) => size > 1024 ** 3 ? (size / 1024 ** 3).toFixed(2) + " GB" : (size / 1024 ** 2).toFixed(1) + " MB";
const timecode = (seconds) => { const n = Math.max(0, Math.floor(seconds || 0)); const h = Math.floor(n / 3600); return (h ? String(h).padStart(2, "0") + ":" : "") + String(Math.floor(n % 3600 / 60)).padStart(2, "0") + ":" + String(n % 60).padStart(2, "0"); };
const getText = (job = state.job) => job ? (job.edited_text ?? job.segments.map((s) => s.resolved_text ?? s.text).join("\n")) : "";

function toast(message) {
  $("toast").textContent = message; $("toast").hidden = false;
  clearTimeout(state.toastTimer); state.toastTimer = setTimeout(() => { $("toast").hidden = true; }, 5200);
}
async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) { let data; try { data = await response.json(); } catch { data = {}; } throw new Error(typeof data.detail === "string" ? data.detail : `请求失败（${response.status}），请稍后重试。`); }
  return response.json();
}
const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
function handle(action) { return async (event) => { try { await action(event); } catch (error) { toast(error.message || "操作失败，请重试。"); } }; }
function canLeave() { return !state.dirty || confirm("有尚未保存的校对内容，确定放弃这些修改吗？"); }
function setView(view) {
  state.view = view;
  for (const key of ["studio", "history", "quiz", "generate", "analysis"]) {
    $(key + "-view").hidden = view !== key;
    $("nav-" + key)?.classList.toggle("active", view === key);
  }
  $("page-label").textContent = {history:"学习书架",studio:"转写工作台",quiz:"课程练习",generate:"AI 出题",analysis:"学习分析"}[view];
  if (typeof renderLearning === "function") renderLearning();
}
function resetSource() {
  if (state.objectURL) URL.revokeObjectURL(state.objectURL);
  state.objectURL = null; state.file = null; $("file-input").value = "";
  const player = $("player"); player.pause(); player.removeAttribute("src"); player.load();
  $("media-section").hidden = true; $("dropzone").hidden = false; $("start-button").disabled = true;
}
function newTask() {
  if (state.uploading || !canLeave()) return;
  state.job = null; state.dirty = false; state.renderedSegments = ""; resetSource();
  $("transcript-editor").value = ""; $("search").value = ""; $("vocabulary").value = "";
  $("model").value = "mimo-v2.5"; $("language").value = "mixed"; $("review-enabled").checked = true; $("ocr-enabled").checked = true;
  setTab("timeline"); updateModelHint(); setView("studio"); renderJob();
}
function showMedia(source, name, size) {
  $("dropzone").hidden = true; $("media-section").hidden = false;
  $("player").src = source; $("selected-filename").textContent = name; $("selected-filename").title = name;
  $("selected-filesize").textContent = size ? formatSize(size) + " · 本地文件" : "本地文件";
}
function chooseFile(file) {
  if (!file || state.uploading || !canLeave()) return;
  const extension = file.name.split(".").pop().toLowerCase();
  if (!["mp4", "mov", "mkv", "webm", "avi", "m4v", "mp3", "wav", "m4a", "aac", "flac", "ogg", "aiff", "aif", "opus", "mpeg", "mpg"].includes(extension)) { toast("请导入常见的视频或音频文件，例如 MP4、MOV、MP3、WAV。"); return; }
  if (file.size > 2 * 1024 ** 3) { toast("文件超过 2 GB，请先压缩或分段。"); return; }
  if (!file.size) { toast("这是一个空文件，请重新选择。"); return; }
  resetSource(); state.file = file; state.job = null; state.dirty = false; state.renderedSegments = "";
  state.objectURL = URL.createObjectURL(file); showMedia(state.objectURL, file.name, file.size);
  $("start-button").disabled = false; $("transcript-editor").value = ""; $("search").value = "";
  setView("studio"); renderJob();
}
function setSourceLock(locked) {
  for (const id of ["start-button", "replace-file", "language", "model", "vocabulary", "new-task", "review-enabled", "ocr-enabled", "upload-book", "upload-category"]) $(id).disabled = locked;
  if (!locked) $("start-button").disabled = !state.file;
  if (!locked) {updateModelHint();if(typeof refreshUploadLocation==='function')refreshUploadLocation();}
}
function upload(form) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest(); xhr.open("POST", "/api/jobs"); xhr.responseType = "json";
    xhr.upload.onprogress = (event) => { if (event.lengthComputable) { $("progress-bar").value = Math.round(event.loaded / event.total * 100); $("progress-detail").textContent = `${Math.round(event.loaded / event.total * 100)}% · 正在把文件导入项目目录，原文件保持原位`; } };
    xhr.onload = () => xhr.status >= 200 && xhr.status < 300 ? resolve(xhr.response) : reject(new Error(typeof xhr.response?.detail === "string" ? xhr.response.detail : "导入失败，请检查文件和剩余磁盘空间。"));
    xhr.onerror = () => reject(new Error("本地服务连接中断，请确认启动窗口仍在运行。"));
    xhr.send(form);
  });
}
async function startTranscription(event) {
  event.preventDefault(); if (!state.file || state.uploading) return;
  const form = new FormData(); form.append("file", state.file); form.append("model", $("model").value); form.append("language", $("language").value); form.append("vocabulary", $("vocabulary").value.trim());
  form.append("book_id", $("upload-book").value); form.append("category_id", $("upload-category").value);
  form.append("review_enabled", $("review-enabled").checked && !$("review-enabled").disabled); form.append("ocr_enabled", $("ocr-enabled").checked && !$("ocr-enabled").disabled);
  state.uploading = true; setSourceLock(true); $("job-progress").hidden = false; $("progress-message").textContent = "正在导入本地文件…"; $("progress-bar").value = 0; $("cancel-button").hidden = true; $("retry-button").hidden = true;
  try {
    const job = await upload(form); state.job = job; state.file = null; state.dirty = false;
    state.renderedSegments = ""; if (state.objectURL) URL.revokeObjectURL(state.objectURL); state.objectURL = null;
    showMedia(`/api/jobs/${job.id}/media`, job.filename, job.size); renderJob(); await refreshJobs(); if(typeof libraryRefresh === "function") await libraryRefresh(); toast("已加入转写队列，可以在学习记录中查看进度。");
  } finally { state.uploading = false; setSourceLock(false); renderJob(); }
}
function statusClass(status) { return status === "completed" ? "success" : status === "failed" ? "error" : activeStatuses.has(status) ? "running" : ""; }
function setTab(tab) {
  state.tab = tab;
  for (const key of ["timeline", "text", "review", "visual"]) { $("tab-" + key).classList.toggle("active", tab === key); $("tab-" + key).setAttribute("aria-selected", String(tab === key)); }
  renderContent();
}
function renderContent() {
  const job = state.job; const segments = job?.segments || [];
  const hasContent = segments.length > 0 || Boolean(job?.edited_text);
  $("empty-output").hidden = hasContent || state.tab !== "timeline";
  $("timeline").hidden = !hasContent || state.tab !== "timeline";
  $("text-view").hidden = !job || state.tab !== "text";
  $("review-view").hidden = state.tab !== "review";
  $("visual-view").hidden = state.tab !== "visual";
  $("review-count").textContent = job?.review_count || 0;
  $("empty-title").textContent = job?.status === "completed" ? "没有检测到语音，可查看画面资料" : "每一个知识点，都值得被记住";
  const query = $("search").value.trim().toLowerCase();
  const key = JSON.stringify([job?.id, segments, job?.visuals, job?.subtitles, job?.status, job?.edited_text, query]);
  if (key !== state.renderedSegments) {
    state.renderedSegments = key;
    const filtered = segments.filter((s) => !query || [s.resolved_text, s.text, s.review_text].join(" ").toLowerCase().includes(query));
    $("timeline").innerHTML = filtered.length ? filtered.map((s) => `<div class="segment"><button class="timestamp" data-time="${s.start}" title="播放此片段">${timecode(s.start)}</button><div><p>${escapeHTML(s.resolved_text ?? s.text)}</p>${s.review_state === "needs_review" ? '<span class="evidence-tag disagreement">与复核结果有分歧 · 请查看复核</span>' : ""}${s.engine ? `<small class="segment-source">${escapeHTML(s.engine)} · 分段时间</small>` : ""}</div></div>`).join("") : '<div class="no-results">没有找到匹配的片段</div>';
    renderEvidence(job, query);
  }
  if (!state.dirty && $("transcript-editor").value !== getText()) $("transcript-editor").value = getText();
  $("transcript-editor").readOnly = job?.status !== "completed";
  $("save-text").disabled = !state.dirty || job?.status !== "completed";
  $("edit-hint").textContent = state.dirty ? "有未保存的修改。复制或导出时会先保存。" : job?.edited_text !== null && job?.edited_text !== undefined ? "校对已保存。时间轴和 SRT 仍保留原始转写。" : "完成后可校对；保存的文本将用于全文导出与出题。";
  const matches = query ? $("transcript-editor").value.toLowerCase().split(query).length - 1 : 0;
  $("search-result").textContent = query ? `全文中找到 ${matches} 处匹配；时间轴按关键词筛选。` : "";
}

function renderEvidence(job, query) {
  const reviewed = (job?.segments || []).filter((s) => s.review_text !== undefined || s.review_state === "pending");
  const matching = reviewed.filter((s) => !query || [s.text, s.review_text].join(" ").toLowerCase().includes(query));
  const needsReview = (s) => ["needs_review", "pending"].includes(s.review_state);
  matching.sort((a, b) => Number(needsReview(b)) - Number(needsReview(a)) || a.start - b.start);
  $("review-view").innerHTML = matching.length ? '<p class="evidence-note">两份独立识别结果供回听比较。分歧不代表哪一份一定正确；语法课的错误例句应保留原样。</p>' + matching.map((s) => `<article class="review-card ${needsReview(s) ? "needs-review" : ""}"><div class="evidence-heading"><button class="timestamp" data-time="${s.start}">${timecode(s.start)}–${timecode(s.end)}</button><span class="evidence-tag">${({needs_review:"待核对",pending:"等待复核",consistent:"结果接近",confirmed:"已人工核对"})[s.review_state] || "未复核"}</span></div><label>主识别 · ${escapeHTML(s.engine || job.model)}</label><p>${escapeHTML(s.raw_text ?? s.text)}</p><label>独立复核 · Whisper Large-v3</label><p>${escapeHTML(s.review_raw_text ?? s.review_text ?? "正在等待复核阶段…")}</p>${job.status === "completed" && needsReview(s) ? `<div class="review-actions"><button class="button secondary small" data-resolve="${s.id}" data-choice="primary">保留主识别</button><button class="button secondary small" data-resolve="${s.id}" data-choice="review" ${s.review_text === undefined || job.edited_text !== null ? "disabled" : ""}>采用复核文本</button><button class="text-button" data-resolve="${s.id}" data-choice="confirm">已在全文核对</button></div>` : ""}</article>`).join("") : `<div class="no-results">${job?.review_enabled ? "复核结果将在第二个识别阶段出现。" : "本任务未启用独立复核。"}</div>`;
  const visuals = (job?.visuals || []).filter((v) => !query || v.text.toLowerCase().includes(query));
  const subtitles = (job?.subtitles || []).filter((s) => !query || s.text.toLowerCase().includes(query));
  $("visual-view").innerHTML = visuals.length || subtitles.length ? '<p class="evidence-note">以下是画面或字幕轨资料，可能包含未说出的例句。它们不会混入讲课全文；Markdown 和出题提示词会分开附带，并标注来源。</p>' + subtitles.map((s) => `<article class="visual-card"><div class="evidence-heading"><button class="timestamp" data-time="${s.start}">${timecode(s.start)}</button><span class="evidence-tag">文件字幕轨</span></div><p>${escapeHTML(s.text)}</p></article>`).join("") + visuals.map((v) => `<article class="visual-card"><div class="evidence-heading"><button class="timestamp" data-time="${v.start}">${timecode(v.start)}</button><span class="evidence-tag">画面 OCR · 需校对</span></div><a href="/api/jobs/${job.id}/frames/${encodeURIComponent(v.image)}" target="_blank" rel="noopener" title="查看原始画面"><img loading="lazy" src="/api/jobs/${job.id}/frames/${encodeURIComponent(v.image)}" alt="${timecode(v.start)} 的课程原始画面"></a><p>${escapeHTML(v.text)}</p></article>`).join("") : `<div class="no-results">${job?.ocr_enabled ? "画面文字将在提取后出现。音频文件和没有文字的视频可能没有结果。" : "本任务未启用画面识别。"}</div>`;
}
function renderJob() {
  const job = state.job;
  const hasContent = Boolean(job && (job.segments.length || getText(job)));
  $("result-status").textContent = job ? labels[job.status] || job.status : "等待导入";
  $("result-status").className = "status-tag " + (job ? statusClass(job.status) : "");
  $("step-transcribe").classList.toggle("current", Boolean(job)); $("step-study").classList.toggle("current", Boolean(job?.status === "completed" && hasContent));
  for (const id of ["copy-text", "copy-study", "export-button", "course-next"]) $(id).disabled = !hasContent;
  if (!state.uploading) {
    $("job-progress").hidden = !job || job.status === "completed";
    if (job) {
      $("progress-message").textContent = job.message;
      if (job.progress === null || ["downloading", "preparing"].includes(job.status)) $("progress-bar").removeAttribute("value"); else $("progress-bar").value = job.progress;
      $("cancel-button").hidden = !activeStatuses.has(job.status); $("retry-button").hidden = !["failed", "cancelled"].includes(job.status);
      const language = { zh: "中文", en: "英语", ja: "日语", ko: "韩语" }[job.detected_language] || job.detected_language;
      $("progress-detail").textContent = job.status === "downloading" ? "首次下载需要联网。模型文件保存在本项目 models/，下载完成后可离线识别。" : job.status === "queued" ? "任务按顺序执行，可继续导入其他课程。" : job.status === "failed" ? "视频已保留，可直接重试。详细日志位于 data/jobs/ 对应任务目录。" : job.status === "cancelled" ? "点击重试可从头重新识别，也可导出已生成的片段。" : [job.duration ? `课程时长 ${timecode(job.duration)}` : "", language ? `检测语言：${language}` : "", job.progress != null ? `${job.progress}%` : "首次加载可能需要一些时间"].filter(Boolean).join(" · ");
    }
  }
  const wordCount = job ? getText(job).replace(/\s/g, "").length : 0;
  $("transcript-meta").textContent = job ? [job.duration ? timecode(job.duration) : "", `${wordCount.toLocaleString()} 字`, job.model, job.elapsed ? `耗时 ${timecode(job.elapsed)}` : ""].filter(Boolean).join(" · ") : "等待一段新的知识旅程";
  renderContent();
  if (typeof renderLearning === "function") renderLearning();
  const notes = [...(job?.warnings || []), ...(job?.review_count ? [`有 ${job.review_count} 个片段待核对，出题提示词会附带未确认说明。`] : []), ...(job?.engine_metrics || []).map((m) => `${m.model} · 实测 GPU 峰值 ${m.gpu_peak_gb} GB · 进程峰值 ${m.rss_peak_gb} GB`)];
  $("job-notices").hidden = !notes.length; $("job-notices").textContent = notes.join("\n");
  const running = job && ["preparing", "transcribing"].includes(job.status);
  $("throttle-notice").hidden = !running;
  if (running) {
    const selected = state.performance?.profile || "slow";
    const pending = job.speed_profile && job.speed_profile !== selected;
    const thermal = {0:"正常",1:"偏热",2:"较热",3:"很热"}[job.thermal_state];
    let activity = pending ? "新档位将在当前计算间隙生效" : job.throttle_state === "thermal_wait" ? "系统热压力偏高，已暂停计算，等待降温" : job.throttle_state === "cooling" ? `正在冷却 · 预计还需约 ${Math.ceil(job.cooldown_remaining || 0)} 秒` : "正在计算";
    $("throttle-notice").textContent = `${speedLabels[selected]}档 · ${activity}${thermal ? ` · 系统热状态：${thermal}` : ""}。模型与识别参数保持不变。`;
  }
}

function renderPerformance() {
  const profile = state.performance?.profile || "slow";
  if (!state.savingSpeed) $("speed-profile").value = profile;
  const descriptions = {fast:"连续计算，优先完成；发热相对更高。",medium:"计算与冷却交替，目标约 35% 工作占空比。",slow:"低热优先，目标约 5% 工作占空比；多数时间冷却，耗时明显增加。"};
  $("speed-description").textContent = descriptions[profile] + " 三档模型、精度、音频与复核参数相同。";
}
async function refreshPerformance() {
  if (state.savingSpeed) return;
  const result = await api("/api/performance");
  if (!state.savingSpeed) { state.performance = result; renderPerformance(); }
}
async function changeSpeed() {
  state.savingSpeed = true; $("speed-profile").disabled = true;
  try {
    state.performance = await api("/api/performance", {method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({profile:$("speed-profile").value})});
    toast(`已切换到${speedLabels[state.performance.profile]}档；模型和识别质量设置不变。`);
  } finally { state.savingSpeed = false; $("speed-profile").disabled = false; renderPerformance(); renderJob(); }
}
async function selectJob(id) {
  if(typeof flushLearningDraft==='function') await flushLearningDraft();
  if (state.uploading || !canLeave()) return;
  const job = await api(`/api/jobs/${id}`);
  if (job.kind === "download") { openModels(); return; }
  resetSource(); state.job = job; state.dirty = false; state.renderedSegments = ""; $("search").value = "";
  $("model").value = job.model; $("language").value = job.language; $("vocabulary").value = job.vocabulary || "";
  $("review-enabled").checked = Boolean(job.review_enabled); $("ocr-enabled").checked = Boolean(job.ocr_enabled);
  showMedia(`/api/jobs/${id}/media`, job.filename, job.size); setView("studio"); renderJob(); updateModelHint();
  if(typeof loadCourseQuizzes === "function") await loadCourseQuizzes();
}
function renderHistory() {
  $("history-count").textContent = state.jobs.filter(j => j.kind === "transcribe").length;
  if (typeof renderShelf === "function") renderShelf();
}
async function refreshJobs() { state.jobs = await api("/api/jobs"); renderHistory(); }
function updateModelHint() {
  const model = state.models.find((m) => m.id === $("model").value);
  if (!model) return;
  $("review-enabled").disabled = state.uploading || model.engine !== "mimo";
  $("ocr-enabled").disabled = state.uploading || !["mimo", "mlx-whisper"].includes(model.engine);
  $("model-hint").textContent = model.ready ? `● 已下载，可离线使用 · ${model.description}` : `首次使用自动下载 ${model.size} · ${model.description}`;
  if (!["mimo", "mlx-whisper"].includes(model.engine)) $("model-hint").textContent += " · 轻量模式仅识别语音";
}
function renderModels() {
  const key = JSON.stringify(state.models);
  if (state.modelsKey === key) return;
  state.modelsKey = key;
  $("models-list").innerHTML = state.models.map((model) => `<div class="model-row"><div><strong>${escapeHTML(model.id)}<span>${model.label} · ${model.size}</span></strong><p>${model.description}</p></div>${model.ready ? `<span class="ready">${icon("check")}已就绪</span>` : `<button class="button secondary small" data-download="${model.id}" ${model.downloading ? "disabled" : ""}>${model.downloading ? "队列处理中…" : icon("download") + "下载"}</button>`}</div>`).join("");
}
async function refreshModels() { state.models = await api("/api/models"); updateModelHint(); renderModels(); }
async function openModels() { if (!$("models-dialog").open) $("models-dialog").showModal(); await refreshModels(); }
async function saveText() {
  if (!state.dirty || !state.job) return;
  const id = state.job.id; const text = $("transcript-editor").value;
  const job = await api(`/api/jobs/${id}/text`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
  if (state.job?.id !== id) return;
  state.job = job; state.dirty = $("transcript-editor").value !== text; renderJob();
}
async function copy(value) {
  // Synchronous selection copy also works in embedded browsers whose async
  // Clipboard API is disconnected from their paste/clipboard integration.
  const previousFocus = document.activeElement;
  const temporary = document.createElement("textarea"); temporary.className = "copy-buffer";
  temporary.value = value; temporary.setAttribute("readonly", ""); document.body.append(temporary); temporary.select();
  let success = false;
  try { success = document.execCommand("copy"); } finally { temporary.remove(); previousFocus?.focus({preventScroll:true}); }
  if (success) return;
  try { await navigator.clipboard.writeText(value); }
  catch { throw new Error("浏览器未允许复制，请使用导出按钮下载文本。"); }
}
async function copyText() { await saveText(); await copy(getText()); toast("课程全文已复制。"); }
async function copyStudy() {
  await saveText();
  const response = await fetch(`/api/jobs/${state.job.id}/export/study`);
  if (!response.ok) throw new Error("生成出题提示词失败，请重试。");
  const text = await response.text();
  $("study-preview").value = text;
  $("study-dialog").showModal();
  try { await copy(text); toast("出题资料已生成并复制，可在预览中核对后粘贴。"); }
  catch { toast("出题资料已生成，可在预览中全选后按 ⌘C 复制。"); }
}
async function exportFile() {
  if (!state.job) return; await saveText();
  const kind = $("export-format").value; const link = document.createElement("a"); link.href = `/api/jobs/${state.job.id}/export/${kind}`; link.download = ""; document.body.append(link); link.click(); link.remove();
  if (kind === "srt" && state.job.edited_text !== null) toast("SRT 使用原始片段与时间戳；校对内容请导出 TXT 或 Markdown。");
}
async function cancelJob(id) { await post(`/api/jobs/${id}/cancel`); await refreshJobs(); if (state.job?.id === id) { state.job = await api(`/api/jobs/${id}`); renderJob(); } await refreshModels(); }
async function deleteJob(id) {
  if (!confirm("删除这条学习记录？该任务的视频副本和转写内容也会删除，原始视频不会受影响。")) return;
  await api(`/api/jobs/${id}`, { method: "DELETE" });
  if (state.job?.id === id) { state.dirty = false; newTask(); }
  await refreshJobs(); toast("学习记录及其本地视频副本已删除。");
}
async function poll() {
  if (state.pollBusy) return; state.pollBusy = true;
  try {
    const health = await api("/api/health"); if (!health.ok) throw new Error("连接失败");
    $("connection-label").textContent = "本地服务已连接";
    const oldStatus = state.job?.status;
    const previouslyActive = state.jobs.some((j) => activeStatuses.has(j.status));
    await refreshPerformance();
    await refreshJobs();
    if (state.job && activeStatuses.has(state.job.status)) {
      const id = state.job.id; const job = await api(`/api/jobs/${id}`);
      if (state.job?.id === id) { state.job = job; renderJob(); if (oldStatus !== job.status && job.status === "completed") { toast(job.message); await refreshModels(); } }
    }
    if ($("models-dialog").open || previouslyActive || state.jobs.some((j) => activeStatuses.has(j.status))) await refreshModels();
  } catch (error) { $("connection-label").textContent = "服务未连接，请检查启动窗口"; }
  finally { state.pollBusy = false; }
}

$("new-task").addEventListener("click", newTask);
$("nav-studio").addEventListener("click", () => setView("studio"));
$("nav-history").addEventListener("click", handle(async () => { setView("history"); await refreshJobs(); if(typeof libraryRefresh === "function") await libraryRefresh(); }));
$("nav-models").addEventListener("click", handle(openModels)); $("manage-models").addEventListener("click", handle(openModels));
$("dropzone").addEventListener("click", () => { if (!state.uploading) $("file-input").click(); });
$("replace-file").addEventListener("click", () => { if (!state.uploading) $("file-input").click(); });
$("file-input").addEventListener("change", (event) => chooseFile(event.target.files[0]));
for (const event of ["dragenter", "dragover"]) $("dropzone").addEventListener(event, (e) => { e.preventDefault(); $("dropzone").classList.add("dragging"); });
for (const event of ["dragleave", "drop"]) $("dropzone").addEventListener(event, (e) => { e.preventDefault(); $("dropzone").classList.remove("dragging"); });
$("dropzone").addEventListener("drop", (event) => { if (event.dataTransfer.files.length > 1) toast("每次导入一个文件，可连续添加到队列。"); chooseFile(event.dataTransfer.files[0]); });
window.addEventListener("dragover", (e) => e.preventDefault()); window.addEventListener("drop", (e) => e.preventDefault());
$("transcribe-form").addEventListener("submit", handle(startTranscription));
$("model").addEventListener("change", updateModelHint);
$("speed-profile").addEventListener("change", handle(changeSpeed));
$("tab-timeline").addEventListener("click", () => setTab("timeline")); $("tab-text").addEventListener("click", () => setTab("text"));
$("tab-review").addEventListener("click", () => setTab("review")); $("tab-visual").addEventListener("click", () => setTab("visual"));
$("search").addEventListener("input", renderContent);
$("transcript-editor").addEventListener("input", () => { state.dirty = $("transcript-editor").value !== getText(); renderContent(); });
$("save-text").addEventListener("click", handle(async () => { await saveText(); toast("校对内容已保存在本机。"); }));
$("copy-text").addEventListener("click", handle(copyText)); $("copy-study").addEventListener("click", handle(copyStudy)); $("export-button").addEventListener("click", handle(exportFile));
$("copy-preview").addEventListener("click", handle(async () => { await copy($("study-preview").value); toast("出题资料已复制。"); }));
$("select-preview").addEventListener("click", () => { $("study-preview").focus(); $("study-preview").select(); });
$("cancel-button").addEventListener("click", handle(() => cancelJob(state.job.id)));
$("retry-button").addEventListener("click", handle(async () => { state.job = await post(`/api/jobs/${state.job.id}/retry`); state.renderedSegments = ""; renderJob(); await refreshJobs(); }));
$("timeline").addEventListener("click", handle(async (event) => { const button = event.target.closest("[data-time]"); if (!button) return; $("player").currentTime = Number(button.dataset.time); try { await $("player").play(); } catch { toast("此格式无法在浏览器播放，可用本地播放器回听对应时间点。"); } }));
for (const id of ["review-view", "visual-view"]) $(id).addEventListener("click", handle(async (event) => {
  const time = event.target.closest("[data-time]");
  if (time) { $("player").currentTime = Number(time.dataset.time); try { await $("player").play(); } catch { toast("请用本地播放器回听这个时间点。"); } return; }
  const resolve = event.target.closest("[data-resolve]");
  if (!resolve || !state.job) return;
  if (state.dirty) { toast("请先保存全文校对，再标记已核对的片段。"); return; }
  state.job = await post(`/api/jobs/${state.job.id}/segments/${resolve.dataset.resolve}/resolve`, {choice:resolve.dataset.choice});
  renderJob(); await refreshJobs(); toast("片段校对已保存。原始两份识别结果仍保留。");
}));
$("history-list").addEventListener("click", handle(async (event) => { const open = event.target.closest("[data-open]"); const remove = event.target.closest("[data-delete]"); const cancel = event.target.closest("[data-cancel]"); if (open) await selectJob(open.dataset.open); if (remove) await deleteJob(remove.dataset.delete); if (cancel) await cancelJob(cancel.dataset.cancel); }));
$("models-list").addEventListener("click", handle(async (event) => { const button = event.target.closest("[data-download]"); if (!button) return; button.disabled = true; try { await post("/api/models/download", { model: button.dataset.download }); await refreshJobs(); await refreshModels(); toast("模型已加入下载队列。可在学习记录中查看或取消。"); } finally { await refreshModels(); } }));
$("help-button").addEventListener("click", () => $("help-dialog").showModal()); $("help-done").addEventListener("click", () => $("help-dialog").close());
for (const button of document.querySelectorAll(".close-dialog")) button.addEventListener("click", () => button.closest("dialog").close());
window.addEventListener("beforeunload", (event) => { if (state.dirty || state.uploading) { event.preventDefault(); event.returnValue = ""; } });
renderJob();
Promise.all([refreshModels(), poll()]).catch((error) => toast(error.message));
setInterval(poll, 2000);
