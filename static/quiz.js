"use strict";
const quizState = {record:null, answers:{}, dirty:false, busy:false, saving:false, timer:null, queue:Promise.resolve(), format:null, sourceFilter:null, edit:0, saveError:null};
const quizTypes = {single_choice:"单选",multiple_choice:"多选",true_false:"判断",fill_blank:"填空",short_answer:"简答"};
const quizStatuses = {correct:"回答正确",incorrect:"回答错误",partial:"部分正确",unanswered:"未作答",manual:"待 AI 评价"};
const qEsc = escapeHTML;

async function quizFormat() {
  if (!quizState.format) quizState.format = await api('/api/quizzes/format');
  return quizState.format;
}
function quizPreview(title, text) {
  $('quiz-preview-title').textContent = title; $('quiz-preview').value = text; $('quiz-format-dialog').showModal();
}
async function quizCopyOrPreview(title, text) {
  quizPreview(title, text);
  try { await copy(text); toast('已复制，可在预览中核对。'); }
  catch { toast('资料已生成，请在预览中全选后复制。'); }
}
async function quizRefresh() {
  const all = await api('/api/quizzes');
  if(typeof learningState!=='undefined') {learningState.allQuizzes=all;renderLearning();}
  const records = quizState.sourceFilter?all.filter(r=>r.source_job_id===quizState.sourceFilter):all;
  $('quiz-library').innerHTML = '<option value="">选择题集，继续上次作答</option>' + records.map(r => `<option value="${qEsc(r.id)}">${qEsc(r.title)} · ${r.answered}/${r.count} 已作答${r.submitted?' · 已提交':''}</option>`).join('');
  $('quiz-library').value = quizState.record?.id || '';
}
function quizSetRecord(record) {
  quizState.record = record; quizState.answers = structuredClone(record.answers); quizState.dirty = false; quizState.saveError = null;
  quizRender();
  if(typeof updateQuizSummary==='function')updateQuizSummary(record);
}
function quizSaveStatus() {
  $('quiz-save-state').textContent = quizState.saveError ? '保存失败：输入仍保留在此页' : quizState.saving ? '正在保存…' : quizState.dirty ? '有未保存作答…' : '作答已保存在本机';
  $('quiz-save-state').classList.toggle('quiz-error', Boolean(quizState.saveError));
  $('quiz-save-retry').hidden = !quizState.saveError;
}
function quizSerialize(task) {
  const pending = quizState.queue.catch(() => {}).then(task);
  quizState.queue = pending;
  return pending;
}
async function quizSaveNow() {
  if (!quizState.record || !quizState.dirty) return;
  const id = quizState.record.id, edit = quizState.edit, answers = structuredClone(quizState.answers);
  quizState.saving = true; quizSaveStatus();
  try {
    const record = await api(`/api/quizzes/${id}/answers`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({answers, revision:quizState.record.revision})});
    quizState.record = record;
    if (edit === quizState.edit) quizState.dirty = false;
    quizState.saveError = null;
  } catch (error) { quizState.saveError = error.message; throw error; }
  finally { quizState.saving = false; quizSaveStatus(); }
}
function quizFlush() { clearTimeout(quizState.timer); return quizSerialize(quizSaveNow); }
function quizIsAnswered(q, value) {
  if (q.type === 'true_false') return typeof value === 'boolean';
  if (Array.isArray(value)) return value.some(v => String(v).trim());
  return value !== null && value !== undefined && String(value).trim() !== '';
}
function quizRenderSummary() {
  const r = quizState.record;
  if (!r) return;
  const count = r.quiz.questions.filter(q => quizIsAnswered(q, quizState.answers[q.id])).length;
  const result = r.result;
  $('quiz-summary').textContent = result ? `自动判分 ${result.auto_score} / ${result.auto_total} 分 · ${result.answered}/${result.count} 题已作答${result.manual_total ? ` · 简答 ${result.manual_total} 分待 AI 评价，不计入自动得分` : ''}` : `${count}/${r.quiz.questions.length} 题已作答 · 提交后显示本地判分，参考答案仍由你选择查看。`;
  for (const q of r.quiz.questions) {
    const status = result?.questions[q.id];
    const node = document.getElementById('quiz-status-' + q.id);
    if (node) { node.className = 'quiz-question-status ' + (status?.status || ''); node.textContent = status ? quizStatuses[status.status] + (status.score !== null ? ` · ${status.score}/${status.points} 分` : '') : ''; }
    for (let i = 0; i < (q.blanks || []).length; i++) {
      const blankStatus = document.getElementById(`quiz-blank-status-${q.id}-${i}`);
      if (blankStatus) {
        const correct = status?.blanks?.[i];
        blankStatus.textContent = correct === undefined ? '' : correct ? '此空正确' : (quizState.answers[q.id]?.[i]?.trim() ? '此空错误' : '此空未作答');
        blankStatus.className = 'quiz-blank-status ' + (correct === undefined ? '' : correct ? 'correct' : 'incorrect');
      }
    }
    const jump = document.getElementById('quiz-jump-' + q.id);
    if (jump) { jump.className = 'quiz-jump ' + (status?.status || (quizIsAnswered(q,quizState.answers[q.id])?'answered':'')); jump.title = status ? quizStatuses[status.status] : quizIsAnswered(q,quizState.answers[q.id]) ? '已作答' : '未作答'; }
  }
  quizSaveStatus();
}
function quizRuleHint(b) {
  const rules = {any_of:'填写一个完整答案；可接受题目规定的同义表达',all_of:'须答全；每个要点用分号或换行分隔，不计顺序',number_range:'只填写数值，不带单位或百分号'};
  return rules[b.mode] + (b.unit ? ` · 单位：${b.unit}` : '') + (b.mode !== 'number_range' ? (b.case_sensitive ? ' · 区分大小写' : ' · 忽略大小写') : '');
}
function quizAnswerText(q) {
  if (q.type === 'true_false') return q.answer ? '正确' : '错误';
  if (q.type === 'single_choice' || q.type === 'multiple_choice') {
    const ids = Array.isArray(q.answer) ? q.answer : [q.answer];
    return q.options.filter(o => ids.includes(o.id)).map(o => `${o.id}. ${o.text}`).join('\n');
  }
  if (q.type === 'fill_blank') return q.blanks.map(b => {
    const a = b.answer;
    return b.label + '：' + (a.mode === 'number_range' ? `${a.min} ≤ 数值 ≤ ${a.max}${a.unit?'（'+a.unit+'）':''}` : (a.mode === 'any_of'?'任一即可：':'必须答全：') + a.values.join(' / '));
  }).join('\n');
  return q.answer;
}
function quizQuestionHTML(q, index) {
  const value = quizState.answers[q.id];
  const qid = qEsc(q.id);
  let fields = '';
  if (q.type === 'single_choice' || q.type === 'multiple_choice') {
    fields = q.options.map(o => `<label class="quiz-option"><input data-question="${qid}" type="${q.type==='multiple_choice'?'checkbox':'radio'}" name="answer-${qid}" value="${qEsc(o.id)}" ${(Array.isArray(value)?value.includes(o.id):value===o.id)?'checked':''}><span><b>${qEsc(o.id)}.</b> ${qEsc(o.text)}</span></label>`).join('');
  } else if (q.type === 'true_false') {
    fields = [true,false].map(v => `<label class="quiz-option"><input type="radio" data-question="${qid}" name="answer-${qid}" value="${v}" ${value===v?'checked':''}><span>${v?'正确':'错误'}</span></label>`).join('');
  } else if (q.type === 'fill_blank') {
    fields = q.blanks.map((b,i) => `<div class="quiz-blank"><label for="blank-${qid}-${i}">${qEsc(b.label)}</label><small id="hint-${qid}-${i}">${qEsc(quizRuleHint(b))}</small><textarea rows="${b.mode==='all_of'?2:1}" id="blank-${qid}-${i}" data-question="${qid}" data-blank="${i}" aria-describedby="hint-${qid}-${i}" ${b.mode==='number_range'?'inputmode="decimal"':''} maxlength="12000">${qEsc(value?.[i] || '')}</textarea><span id="quiz-blank-status-${qid}-${i}" class="quiz-blank-status"></span></div>`).join('');
  } else {
    fields = `<label class="sr-only" for="short-${qid}">第 ${index+1} 题简答</label><textarea id="short-${qid}" rows="5" data-question="${qid}" maxlength="30000" placeholder="用自己的话解释，提交后可导出给 AI 评价。">${qEsc(value || '')}</textarea>`;
  }
  const revealed = quizState.record.revealed.includes(q.id);
  const seen = (quizState.record.seen || []).includes(q.id);
  return `<article class="panel quiz-question" id="question-${qid}"><div class="quiz-question-top"><span class="quiz-type">${index+1} / ${quizTypes[q.type]} · ${q.points} 分</span><span id="quiz-status-${qid}" class="quiz-question-status"></span></div><fieldset><legend>${qEsc(q.prompt)}</legend>${q.type==='multiple_choice'?'<p class="quiz-input-note">选择全部正确选项，少选或多选不得分。</p>':''}${fields}</fieldset><div class="quiz-question-bottom"><button class="text-button" data-reveal="${qid}">${revealed?'隐藏此题答案':'查看此题答案与解析'}</button><button class="text-button" data-clear-question="${qid}">清除此题作答</button>${seen?'<span>曾查看答案</span>':''}</div>${revealed?`<div class="quiz-solution"><strong>参考答案${q.type==='short_answer'?' · 待 AI 评价':''}</strong><p>${qEsc(quizAnswerText(q))}</p><strong>解析</strong><p>${qEsc(q.explanation)}</p></div>`:''}</article>`;
}
function quizRender() {
  const r = quizState.record;
  $('quiz-empty').hidden = Boolean(r); $('quiz-session').hidden = !r;
  if (!r) return;
  $('quiz-title').textContent = r.quiz.title; $('quiz-description').textContent = r.quiz.description || '答案与解析在每道题下方展开，无需翻到页面末尾。';
  $('quiz-questions').innerHTML = r.quiz.questions.map(quizQuestionHTML).join('');
  $('quiz-jumps').innerHTML = r.quiz.questions.map((q,i) => `<a class="quiz-jump" id="quiz-jump-${qEsc(q.id)}" href="#question-${qEsc(q.id)}" aria-label="跳到第 ${i+1} 题">${i+1}</a>`).join('');
  quizRenderSummary(); quizLock(quizState.busy);
}
function quizLock(locked) {
  for (const el of document.querySelectorAll('#quiz-session button, #quiz-questions fieldset, #quiz-library, #quiz-import-button')) el.disabled = locked;
}
async function quizAction(action) {
  if (quizState.busy) return;
  quizState.busy = true; quizLock(true);
  try { await quizFlush(); await action(); }
  finally { quizState.busy = false; quizLock(false); }
}
async function quizOpen(id) {
  if (!id) return;
  await quizAction(async () => { if(typeof flushLearningDraft==='function')await flushLearningDraft(); const record=await api(`/api/quizzes/${id}`); quizSetRecord(record); if(typeof adoptQuizCourse==='function')await adoptQuizCourse(record); $('quiz-import-panel').open = false; });
}
function quizChanged(event) {
  const input = event.target.closest('[data-question]');
  if (!input || quizState.busy) return;
  const q = quizState.record.quiz.questions.find(q => q.id === input.dataset.question);
  let value;
  if (q.type === 'multiple_choice') value = Array.from(input.closest('fieldset').querySelectorAll('input:checked'), el => el.value);
  else if (q.type === 'true_false') value = input.value === 'true';
  else if (q.type === 'fill_blank') value = Array.from(input.closest('fieldset').querySelectorAll('textarea'), el => el.value);
  else value = input.value;
  quizState.answers[q.id] = value; quizState.dirty = true; quizState.edit++; quizState.record.result = null;
  quizRenderSummary(); clearTimeout(quizState.timer);
  quizState.timer = setTimeout(() => { quizFlush().catch(error => toast(error.message)); }, 500);
}
async function quizReveal(question_id, show) {
  const id = quizState.record.id;
  const record = await post(`/api/quizzes/${id}/reveal`, {question_id,show});
  quizSetRecord(record);
}
async function quizDownload(kind) {
  const link = document.createElement('a'); link.href = `/api/quizzes/${quizState.record.id}/export/${kind}`; link.download = ''; document.body.append(link); link.click(); link.remove();
}

$('nav-quiz').addEventListener('click',handle(async () => {quizState.sourceFilter=null;setView('quiz'); await quizRefresh();}));
$('quiz-refresh').addEventListener('click',handle(quizRefresh));
$('quiz-library').addEventListener('change',handle(async event => {const id=event.target.value; try {await quizOpen(id);setView('quiz');} finally {event.target.value=quizState.record?.id || '';}}));
$('quiz-copy-format').addEventListener('click',handle(async () => quizCopyOrPreview('AI 出题格式',(await quizFormat()).prompt)));
$('quiz-show-format').addEventListener('click',handle(async () => quizPreview('AI 出题格式',(await quizFormat()).prompt)));
$('quiz-load-example').addEventListener('click',handle(async () => {if ($('quiz-import-text').value.trim() && !confirm('用示例替换导入框中的文本？已导入的题集不受影响。')) return; $('quiz-import-text').value=JSON.stringify((await quizFormat()).example,null,2); $('quiz-import-error').textContent=''; toast('示例已填入，点击「导入并开始答题」体验。');}));
$('quiz-file-button').addEventListener('click',() => $('quiz-file').click());
$('quiz-file').addEventListener('change',handle(async event => {const file=event.target.files[0]; if (!file) return; try {if (file.size>1000000) throw new Error('JSON 文件超过 1 MB，请分批导入。'); if ($('quiz-import-text').value.trim() && !confirm('用文件内容替换导入框中的文本？')) return; $('quiz-import-text').value=await file.text(); $('quiz-import-error').textContent='';} finally {event.target.value='';}}));
$('quiz-import-button').addEventListener('click',handle(async () => {
  $('quiz-import-error').textContent='';
  try {
    await quizAction(async () => {const content=$('quiz-import-text').value; if (!content.trim()) throw new Error('请先粘贴 AI 返回的 JSON，或填入示例。'); if(typeof flushLearningDraft==='function')await flushLearningDraft(); const r=await post('/api/quizzes',{content,source_job_id:quizState.sourceFilter||null}); quizSetRecord(r); $('quiz-import-text').value=''; $('quiz-import-panel').open=false; await quizRefresh(); $('quiz-session').scrollIntoView({block:'start'}); toast('题集已导入，答案默认隐藏。');});
  } catch(error) {$('quiz-import-error').textContent=error.message; throw error;}
}));
$('quiz-questions').addEventListener('input',quizChanged);
$('quiz-questions').addEventListener('change',event => {if (event.target.tagName==='INPUT') quizChanged(event);});
$('quiz-questions').addEventListener('click',handle(async event => {
  const button=event.target.closest('[data-reveal], [data-clear-question]'); if (!button) return;
  await quizAction(async () => {
    if (button.dataset.reveal) {const id=button.dataset.reveal; await quizReveal(id,!quizState.record.revealed.includes(id));}
    else {delete quizState.answers[button.dataset.clearQuestion]; quizState.dirty=true; quizState.edit++; quizState.record.result=null; await quizSaveNow(); quizRender();}
  });
}));
async function quizSubmit() { return quizAction(async () => {
  const r=quizState.record;
  quizSetRecord(await post(`/api/quizzes/${r.id}/submit`,{answers:quizState.answers,revision:r.revision})); await quizRefresh(); toast('已判分。每题旁可查看结果，答案仍按你的选择展开。');
}); }
$('quiz-submit').addEventListener('click',handle(quizSubmit));
$('quiz-submit-bottom').addEventListener('click',handle(quizSubmit));
$('quiz-reveal-all').addEventListener('click',handle(async () => quizAction(() => quizReveal(null,true))));
$('quiz-hide-all').addEventListener('click',handle(async () => quizAction(() => quizReveal(null,false))));
$('quiz-reset').addEventListener('click',handle(async () => {if (!confirm('清空此题集的全部作答和判分？题目会保留；曾查看答案的标记保留。建议先导出当前作答。')) return; await quizAction(async () => {quizState.answers={};quizState.dirty=true;quizState.edit++;await quizSaveNow();await quizReveal(null,false);await quizRefresh();});}));
$('quiz-save-retry').addEventListener('click',handle(quizFlush));
for (const kind of ['responses','answers','json']) $('quiz-export-'+kind).addEventListener('click',handle(async () => quizAction(() => quizDownload(kind))));
$('quiz-copy-responses').addEventListener('click',handle(async () => quizAction(async () => {const r=await fetch(`/api/quizzes/${quizState.record.id}/export/responses`);if(!r.ok)throw new Error('生成作答资料失败');await quizCopyOrPreview('我的作答 · 给 AI 评价',await r.text());})));
$('quiz-preview-copy').addEventListener('click',handle(async () => {await copy($('quiz-preview').value);toast('文本已复制。');}));
$('quiz-preview-select').addEventListener('click',() => {$('quiz-preview').focus();$('quiz-preview').select();});
window.addEventListener('beforeunload',event => {if(quizState.dirty || quizState.saving){event.preventDefault();event.returnValue='';}});
