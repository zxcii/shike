"use strict";
const learningState={allQuizzes:[],loading:null,feedbackId:null,feedbackDirty:false,feedbackSaving:false,draftCourseId:null,questionDrafts:{}};
function quizzesOfCourse() {return state.job?learningState.allQuizzes.filter(q=>q.source_job_id===state.job.id):[];}
async function loadCourseQuizzes() {
  const records=await api('/api/quizzes');learningState.allQuizzes=records;
  renderLearning();if(state.view==='generate')renderCourseGeneration();if(state.view==='analysis')renderCourseAnalysis();if(state.view==='history')renderShelf();
}
function renderLearning() {
  if(typeof aiRender==='function')aiRender();
  const job=state.job;
  if(learningState.draftCourseId!==job?.id){
    if(learningState.draftCourseId)learningState.questionDrafts[learningState.draftCourseId]=$('course-question-json').value;
    learningState.draftCourseId=job?.id||null;
    $('course-question-json').value=learningState.questionDrafts[job?.id]||'';
    $('course-import-error').textContent='';
    refreshUploadLocation();
  }
  const linked=quizState.record?.source_job_id===job?.id;
  const visible=Boolean(job && state.view!=='history' && (state.view!=='quiz'||quizState.sourceFilter===job.id||linked) && (state.view!=='analysis'||linked));
  $('quiz-view').classList.toggle('course-practice-mode',Boolean(job && (quizState.sourceFilter===job.id||linked)));
  $('course-context').hidden=!visible;$('new-course-workflow').hidden=Boolean(job);
  $('quiz-source-banner').hidden=!quizState.record;
  if(quizState.record)$('quiz-source-banner').textContent=quizState.record.source_job_id?`所属课程：${quizState.record.source?.title||'已关联课程'} · 题集与本课关联保存`:'这份题集尚未关联课程，可在课程的「让 AI 出题」中关联已有题集。';
  if(!job)return;
  $('course-current-title').textContent=courseTitle(job);$('course-location').textContent=courseLocation(job.id);
  const phase={studio:'learn',generate:'generate',quiz:'practice',analysis:'analysis'}[state.view];
  const quizzes=quizzesOfCourse();
  for(const key of ['learn','generate','practice','analysis']){
    const button=$('course-step-'+key);button.classList.toggle('active',key===phase);button.setAttribute('aria-current',key===phase?'step':'false');
    button.disabled=(key==='generate' && (job.status!=='completed'||!getText(job)))||(['practice','analysis'].includes(key)&&!quizzes.length);
  }
  $('course-quiz-count').textContent=quizzes.length?`${quizzes.length} 份本课练习`:'自测与即时核对';
  $('quiz-source-banner').hidden=!quizState.record;
  if(quizState.record)$('quiz-source-banner').textContent=quizState.record.source_job_id?`所属课程：${quizState.record.source?.title||'已关联课程'} · 题集与本课关联保存`:'这份题集尚未关联课程，可在课程的「让 AI 出题」中关联已有题集。';
}
async function markCourseLearned() {
  const p=coursePlacement(state.job.id);
  await libPatch(`/api/library/courses/${state.job.id}`,{book_id:p.book_id,category_id:p.category_id,title:p.title,learned:true});await libraryRefresh();
}
async function showCoursePhase(phase) {
  if(!state.job){toast('请先从书架打开一节课程。');return;}
  await flushLearningDraft();
  if(phase==='learn'){setView('studio');return;}
  if(phase==='generate'){
    if(state.job.status!=='completed'||!getText()){toast('本课转写完成并有内容后，即可生成出题资料。');return;}
    await saveText();await loadCourseQuizzes();setView('generate');renderCourseGeneration();return;
  }
  await loadCourseQuizzes();const quizzes=quizzesOfCourse();
  if(!quizzes.length){setView('generate');renderCourseGeneration();toast('先为本课导入一份 AI 题集。');return;}
  quizState.sourceFilter=state.job.id;
  if(quizState.record?.source_job_id!==state.job.id)await quizOpen(quizzes[0].id);
  await quizRefresh();
  if(phase==='practice'){setView('quiz');return;}
  await quizFlush();setView('analysis');renderCourseAnalysis();
}
function renderCourseGeneration() {
  const quizzes=quizzesOfCourse();
  $('course-quizzes').innerHTML=quizzes.length?quizzes.map(q=>`<article class="course-quiz-row"><div><strong>${escapeHTML(q.title)}</strong><small>${q.answered}/${q.count} 已作答 · ${q.submitted?'已提交判分':'尚未提交'}${q.has_analysis?' · 已保存分析':''}</small></div><button class="button secondary small" data-course-quiz="${q.id}">继续答题</button><button class="text-button" data-course-analysis="${q.id}">查看分析</button></article>`).join(''):'<div class="course-quiz-empty">本课还没有题集。将 AI 返回的 JSON 粘贴到上方，即可开始第一份练习。</div>';
  $('course-link-quiz').innerHTML='<option value="">选择尚未关联的题集</option>'+learningState.allQuizzes.filter(q=>!q.source_job_id).map(q=>`<option value="${q.id}">${escapeHTML(q.title)}</option>`).join('');
  const ready=Boolean(state.job?.status==='completed' && getText());$('copy-study').disabled=!ready;$('course-import-quiz').disabled=!ready;
}
function updateQuizSummary(record) {
  const entry={id:record.id,title:record.quiz.title,count:record.quiz.questions.length,answered:record.quiz.questions.filter(q=>quizIsAnswered(q,record.answers[q.id])).length,submitted:Boolean(record.result),source_job_id:record.source_job_id,has_analysis:Boolean(record.analysis),updated_at:record.updated_at};
  const index=learningState.allQuizzes.findIndex(q=>q.id===record.id);if(index<0)learningState.allQuizzes.unshift(entry);else learningState.allQuizzes[index]=entry;
  renderLearning();
}
async function adoptQuizCourse(record) {
  if(record.source_job_id && state.job?.id!==record.source_job_id){
    try{await selectJob(record.source_job_id);}catch(error){if(error.message==='记录不存在')toast('原课程已删除，题集和出题时的课程快照仍保留。');else throw error;}
  }
  if(record.source_job_id)quizState.sourceFilter=record.source_job_id;
}
function renderCourseAnalysis() {
  const record=quizState.record, quizzes=quizzesOfCourse();
  $('analysis-quiz').innerHTML=quizzes.map(q=>`<option value="${q.id}" ${q.id===record?.id?'selected':''}>${escapeHTML(q.title)}</option>`).join('');
  const ready=Boolean(record && record.source_job_id===state.job?.id);
  $('analysis-empty').hidden=ready;$('analysis-content').hidden=!ready;if(!ready)return;
  const result=record.result;
  const wrong=result?Object.entries(result.questions).filter(([,r])=>['incorrect','partial','unanswered','manual'].includes(r.status)):[];
  const completed=result?Object.values(result.questions).filter(r=>r.status==='correct').length:0;
  $('analysis-stats').innerHTML=[['自动判分',result?`${result.auto_score} / ${result.auto_total}`:'尚未提交'],['完全答对',result?`${completed} / ${result.count} 题`:'先完成练习'],['待 AI 评价',result?`${result.manual_total} 分`:'简答需 AI 评价'],['参考答案提示',`${(record.seen||[]).length} 题曾查看`]].map(([label,value])=>`<div class="panel"><span>${label}</span><strong>${escapeHTML(value)}</strong></div>`).join('');
  $('analysis-mistakes').innerHTML=!result?'<p>本题集还未提交。先完成答题，再查看错题与本地判分。</p>':wrong.length?wrong.map(([id,r])=>{
    const index=record.quiz.questions.findIndex(q=>q.id===id), q=record.quiz.questions[index];
    return `<button class="analysis-question" data-analysis-question="${escapeHTML(id)}"><span>${index+1}. ${escapeHTML(q.prompt)}</span><small>${quizStatuses[r.status]}${r.score!==null?' · '+r.score+'/'+r.points+' 分':''} ↗</small></button>`;
  }).join(''):'<p>本地可判分题已全部答对。可以请 AI 继续考察迁移与应用能力。</p>';
  if(learningState.feedbackId!==record.id || !learningState.feedbackDirty){$('analysis-feedback').value=record.analysis?.text||'';learningState.feedbackId=record.id;learningState.feedbackDirty=false;}
  $('analysis-stale').hidden=!record.analysis_stale;
  $('analysis-save-state').textContent=learningState.feedbackDirty?'有未保存反馈':record.analysis?'分析已保存':'等待你的反馈与复习计划';
  if(typeof aiRender==='function')aiRender();
}
async function saveCourseFeedback() {
  if(!quizState.record || !learningState.feedbackDirty)return;
  if(learningState.feedbackSaving)throw new Error('分析正在保存，请稍后重试。');
  if(learningState.feedbackId!==quizState.record.id)throw new Error('请先回到刚才的题集保存反馈。');
  learningState.feedbackSaving=true;$('analysis-save').disabled=true;$('analysis-feedback').disabled=true;
  try{
    await quizFlush();const r=quizState.record;
    const saved=await libPatch(`/api/quizzes/${r.id}/analysis`,{text:$('analysis-feedback').value,revision:r.revision});
    quizState.record=saved;learningState.feedbackDirty=false;updateQuizSummary(saved);renderCourseAnalysis();
  }finally{learningState.feedbackSaving=false;$('analysis-save').disabled=false;$('analysis-feedback').disabled=false;}
}
async function flushLearningDraft() {if(learningState.feedbackDirty)await saveCourseFeedback();}
$('course-next').addEventListener('click',handle(async()=>{await markCourseLearned();await showCoursePhase('generate');}));
for(const phase of ['learn','generate','practice','analysis'])$('course-step-'+phase).addEventListener('click',handle(()=>showCoursePhase(phase)));
$('course-organize').addEventListener('click',()=>{if(state.job)organizeCourse(state.job.id);});
$('course-back-shelf').addEventListener('click',handle(async()=>{await flushLearningDraft();const p=coursePlacement(state.job.id);await openBook(p.book_id||'unfiled');}));
$('course-import-quiz').addEventListener('click',handle(async()=>{
  $('course-import-error').textContent='';$('course-import-quiz').disabled=true;
  try{await flushLearningDraft();await quizFlush();const record=await post('/api/quizzes',{content:$('course-question-json').value,source_job_id:state.job.id});quizState.sourceFilter=state.job.id;quizSetRecord(record);$('quiz-import-panel').open=false;$('course-question-json').value='';await quizRefresh();setView('quiz');toast('题集已归入本课，开始自测吧。');}
  catch(error){$('course-import-error').textContent=error.message;throw error;}finally{$('course-import-quiz').disabled=false;}
}));
$('course-quizzes').addEventListener('click',handle(async event=>{const answer=event.target.closest('[data-course-quiz]'),analysis=event.target.closest('[data-course-analysis]');if(answer||analysis){await flushLearningDraft();quizState.sourceFilter=state.job.id;await quizOpen(answer?.dataset.courseQuiz||analysis.dataset.courseAnalysis);await showCoursePhase(answer?'practice':'analysis');}}));
$('course-link-button').addEventListener('click',handle(async()=>{
  const id=$('course-link-quiz').value;if(!id){toast('请选择一份未关联题集。');return;}
  await flushLearningDraft();await quizFlush();const record=await api(`/api/quizzes/${id}`);
  const saved=await libPatch(`/api/quizzes/${id}/source`,{source_job_id:state.job.id,revision:record.revision});
  quizState.sourceFilter=state.job.id;quizSetRecord(saved);await loadCourseQuizzes();renderCourseGeneration();toast('已有题集已关联到本课。');
}));
$('quiz-next-analysis').addEventListener('click',handle(async()=>{if(!quizState.record?.source_job_id){toast('请先在课程的「让 AI 出题」中关联这份题集。');return;}await adoptQuizCourse(quizState.record);await showCoursePhase('analysis');}));
$('analysis-quiz').addEventListener('change',handle(async event=>{await flushLearningDraft();await quizOpen(event.target.value);setView('analysis');renderCourseAnalysis();}));
$('analysis-practice').addEventListener('click',handle(()=>showCoursePhase('practice')));$('analysis-relearn').addEventListener('click',handle(()=>showCoursePhase('learn')));
$('analysis-mistakes').addEventListener('click',handle(async event=>{const button=event.target.closest('[data-analysis-question]');if(button){await showCoursePhase('practice');document.getElementById('question-'+button.dataset.analysisQuestion)?.scrollIntoView({block:'start'});}}));
$('analysis-copy').addEventListener('click',handle(async()=>{await quizFlush();const r=await fetch(`/api/quizzes/${quizState.record.id}/export/responses`);if(!r.ok)throw new Error('生成分析资料失败');await quizCopyOrPreview('本课作答与分析资料',await r.text());}));
$('analysis-export').addEventListener('click',handle(async()=>{await quizFlush();await quizDownload('responses');}));
$('analysis-feedback').addEventListener('input',()=>{learningState.feedbackDirty=true;$('analysis-save-state').textContent='有未保存反馈';});
$('analysis-save').addEventListener('click',handle(async()=>{await saveCourseFeedback();toast('分析与复习计划已保存到本课。');}));
window.addEventListener('beforeunload',event=>{if(learningState.feedbackDirty){event.preventDefault();event.returnValue='';}});
loadCourseQuizzes().catch(error=>toast(error.message));renderLearning();
