"use strict";
const aiState={settings:null,editing:null,tasks:[],handled:new Set(),pollBusy:false,saving:false,catalogRequest:0};
const aiActive=task=>['queued','running'].includes(task.status);
function aiProfiles(){return aiState.settings?.profiles||[];}
function aiConnection(id){return aiProfiles().find(p=>p.id===id);}
function aiFillSelectors(){
  for(const id of ['ai-generate-profile','ai-analysis-profile']){
    const select=$(id),previous=select.value,preferred=aiProfiles().some(p=>p.id===previous)?previous:aiState.settings?.default_profile_id;
    select.innerHTML=aiProfiles().length?aiProfiles().map(p=>`<option value="${p.id}">${escapeHTML(p.name)} · ${escapeHTML(p.model)}</option>`).join(''):'<option value="">请先添加模型供应商连接</option>';
    if(preferred)select.value=preferred;
  }
}
async function aiRefreshSettings(){
  aiState.settings=await api('/api/ai/settings');aiFillSelectors();aiRenderProfileList();aiRender();
}
function aiRenderProfileList(){
  $('provider-list').innerHTML=aiProfiles().length?aiProfiles().map(p=>`<button class="provider-card ${p.id===aiState.editing?'active':''}" data-profile="${p.id}"><strong>${escapeHTML(p.name)}${p.id===aiState.settings.default_profile_id?' · 默认':''}</strong><small>${escapeHTML(p.provider)} / ${escapeHTML(p.model)}<br>${p.has_key?'密钥已保存':'未保存 API Key'}${p.has_provider_params?' · 另有供应商参数':''}</small></button>`).join(''):'<p class="provider-empty">尚未配置连接。右侧填写后即可调用；也可继续用手动出题。</p>';
}
async function aiOpenSettings(){
  await aiRefreshSettings();
  $('provider-options').innerHTML=aiState.settings.providers.map(p=>`<option value="${escapeHTML(p)}"></option>`).join('');
  $('provider-preset').innerHTML=aiState.settings.presets.map(p=>`<option value="${p.id}">${escapeHTML(p.name)}</option>`).join('');
  aiEditProfile(aiState.editing||aiState.settings.default_profile_id||null);$('providers-dialog').showModal();
}
function aiEditProfile(identity=null){
  const p=aiConnection(identity);aiState.editing=p?.id||null;
  $('provider-name').value=p?.name||'';$('provider-type').value=p?.provider||'';$('provider-model').value=p?.model||'';$('provider-base-url').value=p?.base_url||'';
  $('provider-key').value='';$('provider-key').placeholder=p?.has_key?'密钥已保存，留空保留；更换服务时需重新填写':'填写供应商密钥；本地服务可不填';
  $('provider-params').value='';$('provider-params').placeholder=p?.has_provider_params?'供应商参数已保存，留空沿用；需要修改时重新填写完整 JSON':'例如：{"api_version":"供应商版本","aws_region_name":"地区"}';
  $('provider-clear-key').checked=false;$('provider-clear-params').checked=false;
  $('provider-max-tokens').value=p?.max_tokens||8192;$('provider-timeout').value=p?.timeout||300;
  $('provider-make-default').checked=!p||p.id===aiState.settings?.default_profile_id;$('provider-preset').value='native';$('provider-error').textContent='';
  $('provider-delete').disabled=!p;$('provider-test').disabled=!p;
  aiRenderProfileList();aiLoadCatalog().catch(()=>{});aiRender();
}
async function aiLoadCatalog(){
  const request=++aiState.catalogRequest,provider=$('provider-type').value.trim();
  $('provider-model-options').innerHTML='';
  if(!aiState.settings?.providers.includes(provider)){$('provider-catalog-note').textContent='从 LiteLLM 供应商目录选择标识后，可加载随包模型参考。';return;}
  const result=await api('/api/ai/catalog?provider='+encodeURIComponent(provider));if(request!==aiState.catalogRequest)return;
  $('provider-model-options').innerHTML=result.models.map(m=>`<option value="${escapeHTML(m)}"></option>`).join('');
  $('provider-catalog-note').textContent=result.models.length?`SDK 目录中有 ${result.models.length} 个对话模型参考，也可手填模型 ID。具体权限以供应商为准。`:'此供应商没有随包对话模型目录，请填写供应商控制台里的模型 ID。';
}
function aiTaskBox(id,task){
  const box=$(id);box.hidden=!task;if(!task)return;
  const tokens=task.usage?.total_tokens;
  box.className='ai-task-box '+task.status;
  box.innerHTML=`<p>${escapeHTML(task.message)}</p><small>${escapeHTML(task.profile_name)} · ${escapeHTML(task.model)}${tokens!==undefined?' · 本次 '+tokens+' tokens':''}</small><div class="quiz-actions">${aiActive(task)?`<button class="text-button" data-ai-cancel="${task.id}">取消等待</button>`:''}${task.result_id?`<button class="text-button" data-ai-result="${task.id}">${task.kind==='generate'?'打开本课题集':'查看已保存分析'}</button>`:''}${task.has_output?`<button class="text-button" data-ai-output="${task.id}">查看模型原始输出</button>`:''}</div>`;
}
function aiLatest(kind,identity){return aiState.tasks.find(t=>t.kind===kind&&(kind==='test'?t.profile_id===identity:kind==='analysis'?t.quiz_id===identity:t.source_job_id===identity));}
function aiRender(){
  const generate=aiConnection($('ai-generate-profile').value),analysis=aiConnection($('ai-analysis-profile').value);
  const task=aiLatest('generate',state.job?.id),analysisTask=aiLatest('analysis',quizState.record?.id),test=aiLatest('test',aiState.editing);
  $('ai-generate-disclosure').textContent=generate?`点击后会将本课转写、画面文字与出题要求发送给「${generate.name}」（${generate.base_url||generate.provider}），可能产生供应商费用；不会发送视频文件。`:'先配置一个连接即可直接出题。语音转写继续在本地运行。';
  $('ai-analysis-disclosure').textContent=analysis?`点击后会将本课内容快照、原题、你的作答及参考答案发送给「${analysis.name}」（${analysis.base_url||analysis.provider}），可能产生供应商费用。`:'先在模型供应商中添加用于分析的模型连接。';
  $('ai-generate').disabled=!generate||!state.job||state.job.status!=='completed'||(task&&aiActive(task))||aiState.generating;
  $('ai-analyze').disabled=!analysis||!quizState.record?.result||(analysisTask&&aiActive(analysisTask))||aiState.analysing;
  $('provider-test').disabled=!aiState.editing||(test&&aiActive(test));
  aiTaskBox('ai-generate-task',task);aiTaskBox('ai-analysis-task',analysisTask);aiTaskBox('ai-test-task',test);
}
async function aiRefreshTasks(initial=false){
  if(aiState.pollBusy)return;aiState.pollBusy=true;
  try{
    aiState.tasks=await api('/api/ai/tasks');
    if(initial)for(const task of aiState.tasks)if(!aiActive(task))aiState.handled.add(task.id);
    aiRender();
    for(const task of aiState.tasks){
      if(aiActive(task)||aiState.handled.has(task.id))continue;aiState.handled.add(task.id);
      if(task.status==='completed'&&task.kind==='generate'&&state.job?.id===task.source_job_id){
        await loadCourseQuizzes();
        if(state.view==='generate')await aiOpenResult(task);
      }else if(task.status==='completed'&&task.kind==='analysis'&&quizState.record?.id===task.quiz_id){
        if(!learningState.feedbackDirty&&!quizState.dirty){const record=await api(`/api/quizzes/${task.quiz_id}`);quizSetRecord(record);if(state.view==='analysis')renderCourseAnalysis();}
        else toast('AI 分析已保存。你在页面里未保存的输入仍保留，可先查看模型输出。');
        await loadCourseQuizzes();
      }
      if(task.kind==='test'||state.job?.id===task.source_job_id)toast(task.message);
    }
  }finally{aiState.pollBusy=false;}
}
async function aiOpenResult(task){
  await flushLearningDraft();await quizFlush();
  const record=await api(`/api/quizzes/${task.result_id}`);
  await adoptQuizCourse(record);quizState.sourceFilter=record.source_job_id;quizSetRecord(record);await quizRefresh();
  if(task.kind==='analysis'){setView('analysis');renderCourseAnalysis();}
  else setView('quiz');
}
async function aiStartGeneration(){
  if(!state.job)return;const jobId=state.job.id;
  aiState.generating=true;aiRender();
  try{await saveText();const task=await post(`/api/ai/courses/${jobId}/generate`,{profile_id:$('ai-generate-profile').value,count:Number($('ai-question-count').value),instructions:$('ai-generation-instructions').value});
    aiState.handled.delete(task.id);aiState.tasks=[task,...aiState.tasks.filter(t=>t.id!==task.id)];aiRender();
  }finally{aiState.generating=false;aiRender();}
}
async function aiStartAnalysis(){
  if(!quizState.record)return;aiState.analysing=true;aiRender();
  try{await flushLearningDraft();await quizFlush();const record=quizState.record;
    const task=await post(`/api/ai/quizzes/${record.id}/analyze`,{profile_id:$('ai-analysis-profile').value,revision:record.revision,instructions:$('ai-analysis-instructions').value});
    aiState.handled.delete(task.id);aiState.tasks=[task,...aiState.tasks.filter(t=>t.id!==task.id)];aiRender();
  }finally{aiState.analysing=false;aiRender();}
}
for(const id of ['provider-settings','ai-generation-settings','ai-analysis-settings'])$(id).addEventListener('click',handle(aiOpenSettings));
$('provider-new').addEventListener('click',()=>aiEditProfile());
$('provider-list').addEventListener('click',event=>{const button=event.target.closest('[data-profile]');if(button)aiEditProfile(button.dataset.profile);});
$('provider-preset').addEventListener('change',handle(async()=>{
  const preset=aiState.settings.presets.find(p=>p.id===$('provider-preset').value);if(!preset)return;
  $('provider-type').value=preset.provider;$('provider-base-url').value=preset.base_url;
  if(!$('provider-name').value)$('provider-name').value=preset.id==='native'?'':preset.name;
  await aiLoadCatalog();
}));
$('provider-type').addEventListener('change',handle(aiLoadCatalog));
$('provider-form').addEventListener('submit',handle(async event=>{
  event.preventDefault();if(aiState.saving)return;aiState.saving=true;$('provider-save').disabled=true;$('provider-error').textContent='';
  try{
    let params;
    if($('provider-params').value.trim()){try{params=JSON.parse($('provider-params').value);}catch{throw new Error('供应商参数不是有效 JSON，请填写对象或留空。');}if(!params||Array.isArray(params)||typeof params!=='object')throw new Error('供应商参数必须是 JSON 对象。');}
    const body={name:$('provider-name').value,provider:$('provider-type').value.trim(),model:$('provider-model').value.trim(),base_url:$('provider-base-url').value.trim(),api_key:$('provider-key').value||undefined,provider_params:params,clear_key:$('provider-clear-key').checked,clear_provider_params:$('provider-clear-params').checked,max_tokens:Number($('provider-max-tokens').value),timeout:Number($('provider-timeout').value)};
    const saved=aiState.editing?await libPatch(`/api/ai/profiles/${aiState.editing}`,body):await post('/api/ai/profiles',body);
    if($('provider-make-default').checked)await libPatch('/api/ai/settings',{default_profile_id:saved.id});
    aiState.editing=saved.id;await aiRefreshSettings();aiEditProfile(saved.id);toast('模型连接已保存在本项目，密钥不会回显。');
  }catch(error){$('provider-error').textContent=error.message;throw error;}finally{aiState.saving=false;$('provider-save').disabled=false;}
}));
$('provider-delete').addEventListener('click',handle(async()=>{if(!aiState.editing||!confirm('删除这份模型连接及其保存的密钥？已有题集、作答和分析会保留。'))return;await api(`/api/ai/profiles/${aiState.editing}`,{method:'DELETE'});aiState.editing=null;await aiRefreshSettings();aiEditProfile();}));
$('provider-test').addEventListener('click',handle(async()=>{if(!aiState.editing)return;const task=await post('/api/ai/test',{profile_id:aiState.editing});aiState.handled.delete(task.id);aiState.tasks=[task,...aiState.tasks.filter(t=>t.id!==task.id)];aiRender();}));
$('ai-generate-profile').addEventListener('change',aiRender);$('ai-analysis-profile').addEventListener('change',aiRender);
$('ai-generate').addEventListener('click',handle(aiStartGeneration));$('ai-analyze').addEventListener('click',handle(aiStartAnalysis));
for(const id of ['ai-generate-task','ai-analysis-task','ai-test-task'])$(id).addEventListener('click',handle(async event=>{
  const cancel=event.target.closest('[data-ai-cancel]'),output=event.target.closest('[data-ai-output]'),result=event.target.closest('[data-ai-result]');
  if(cancel){await post(`/api/ai/tasks/${cancel.dataset.aiCancel}/cancel`);await aiRefreshTasks();}
  if(output){const response=await fetch(`/api/ai/tasks/${output.dataset.aiOutput}/output`);if(!response.ok)throw new Error('读取模型输出失败');quizPreview('模型原始输出（可能包含参考答案）',await response.text());}
  if(result){const task=aiState.tasks.find(t=>t.id===result.dataset.aiResult);if(task)await aiOpenResult(task);}
}));
Promise.all([aiRefreshSettings(),aiRefreshTasks(true)]).catch(error=>toast(error.message));
setInterval(()=>{if(aiState.tasks.some(aiActive))aiRefreshTasks().catch(error=>toast(error.message));},1800);
