"use strict";
const settingsState={data:null,applied:false,template:null,busy:false};
const trustNames={zh:'中文',en:'英文',mixed:'中英混合',other:'其他语言'};
const modelValue=(value)=>value?.startsWith('local:')?value.slice(6):value;
function fillModelSelect(select,options,value,stripLocal=false){
  const values=options.map(o=>({value:stripLocal?modelValue(o.value):o.value,label:o.label}));
  if(value&&!values.some(o=>o.value===value))values.push({value,label:'连接已不可用 · 请重新选择'});
  select.innerHTML=values.map(o=>`<option value="${escapeHTML(o.value)}">${escapeHTML(o.label)}</option>`).join('');
  if(value)select.value=value;
}
async function refreshAppSettings(apply=false){
  settingsState.data=await api('/api/settings');
  const options=settingsState.data.model_options;
  fillModelSelect($('model'),options.speech,$('model').value,true);
  fillModelSelect($('review-model'),options.review,$('review-model').value||settingsState.data.settings.models.review);
  fillModelSelect($('vision-model'),options.vision,$('vision-model').value||settingsState.data.settings.models.vision);
  if(apply&&!settingsState.applied&&!state.job&&!state.file){applySavedDefaults();settingsState.applied=true;}
  updateModelHint();if(typeof aiFillSelectors==='function'){aiFillSelectors();aiRender();}
}
function applySavedDefaults(){
  const config=settingsState.data?.settings;if(!config)return;
  $('model').value=modelValue(config.models.speech);$('review-model').value=config.models.review;$('vision-model').value=config.models.vision;
  $('review-enabled').checked=config.review_enabled;$('ocr-enabled').checked=config.ocr_enabled;updateModelHint();
}
function restoreJobModels(job){
  if(!settingsState.data)return;
  fillModelSelect($('model'),settingsState.data.model_options.speech,job.model,true);
  fillModelSelect($('review-model'),settingsState.data.model_options.review,job.review_binding?.value||'local:'+(job.review_model||'whisper-large-v3-mlx'));
  fillModelSelect($('vision-model'),settingsState.data.model_options.vision,job.vision_binding?.value||'local:vision-ocr');
}
function renderComputeSettings(){
  const local=(value)=>value&&!value.startsWith('provider:');
  const native=local($('model').value)||($('review-enabled').checked&&local($('review-model').value))||($('ocr-enabled').checked&&local($('vision-model').value));
  $('speed-settings').hidden=state.job?.uses_local_compute===false||!native;
  $('review-model').disabled=state.uploading||!$('review-enabled').checked;$('vision-model').disabled=state.uploading||!$('ocr-enabled').checked;
  const config=state.job||settingsState.data?.settings;
  $('review-policy-hint').textContent=!$('review-enabled').checked?'本次不复核，仅采用主转写。':config?.review_policy==='by_language'?`按语言默认采用：${Object.entries(trustNames).map(([key,name])=>name+' → '+(config.trust?.[key]==='review'?'复核':'主转写')).join('；')}。可回听并改选。`:'保留主转写，分歧在复核页由你核对。';
  const primary=$('model').value.startsWith('provider:')?$('model').value:'local:'+$('model').value;
  if($('review-enabled').checked&&primary===$('review-model').value)$('review-policy-hint').textContent+=' 主转写与复核使用同一模型，不能提供不同模型间的交叉验证。';
  const outgoing=[];
  if(!local($('model').value))outgoing.push('音频片段（主转写）');
  if($('review-enabled').checked&&!local($('review-model').value))outgoing.push('音频片段（复核）');
  if($('ocr-enabled').checked&&!local($('vision-model').value))outgoing.push('抽取的课程画面');
  $('source-disclosure').textContent=outgoing.length?'点击开始后将发送'+outgoing.join('、')+'至所选模型服务；可能产生供应商费用。原视频副本保存在本项目。':'本次使用本地模型，识别资料保存在本项目。';
}
function settingsPage(page){for(const key of ['defaults','prompts'])$('settings-'+key).hidden=page!==key;document.querySelectorAll('[data-settings-tab]').forEach(b=>b.classList.toggle('active',b.dataset.settingsTab===page));}
function renderSettingsForm(){
  const data=settingsState.data,config=data.settings;
  $('default-review').checked=config.review_enabled;$('default-ocr').checked=config.ocr_enabled;$('default-review-policy').value=config.review_policy;
  $('language-trust').innerHTML=Object.entries(trustNames).map(([key,name])=>`<label>${name}默认采用<select id="trust-${key}"><option value="primary">主转写模型</option><option value="review">独立复核模型</option></select></label>`).join('');
  for(const key in trustNames)$('trust-'+key).value=config.trust[key];
  $('function-models').innerHTML=Object.entries(data.functions).map(([key,name])=>`<label>${escapeHTML(name)}<select id="setting-model-${key}" aria-label="${escapeHTML(name)}默认模型"></select></label>`).join('');
  for(const key in data.functions)fillModelSelect($('setting-model-'+key),data.model_options[key],config.models[key]);
  $('prompt-purpose').innerHTML=Object.entries(data.purposes).map(([key,name])=>`<option value="${key}">${escapeHTML(name)}</option>`).join('');
  renderTemplateList();renderTrustEnabled();$('settings-save-state').textContent='';
}
function renderTrustEnabled(){
  const enabled=$('default-review').checked&&$('default-review-policy').value==='by_language';
  for(const key in trustNames)$('trust-'+key).disabled=!enabled;
  $('language-trust').classList.toggle('inactive',!enabled);
}
function renderTemplateList(selected){
  const purpose=$('prompt-purpose').value,config=settingsState.data.settings;
  $('prompt-template').innerHTML=config.templates[purpose].map(t=>`<option value="${t.id}">${escapeHTML(t.name)}${config.active_templates[purpose]===t.id?' · 当前默认':''}</option>`).join('');
  $('prompt-template').value=selected||config.active_templates[purpose];renderTemplate();
}
function renderTemplate(){
  const purpose=$('prompt-purpose').value,config=settingsState.data.settings,entry=config.templates[purpose].find(t=>t.id===$('prompt-template').value);if(!entry)return;
  settingsState.template=entry;$('prompt-name').value=entry.name;$('prompt-text').value=entry.text;$('prompt-save').disabled=entry.id==='builtin';
  $('prompt-active-note').textContent=`当前默认：${config.templates[purpose].find(t=>t.id===config.active_templates[purpose])?.name}。${entry.id==='builtin'?'可直接编辑正文，再另存为自己的模板。':'保存后会用于后续生成的资料或调用。'}`;$('prompt-save-state').textContent='';
}
async function openAppSettings(){await refreshAppSettings();renderSettingsForm();settingsPage('defaults');$('app-settings-dialog').showModal();}
async function saveDefaults(body){
  settingsState.data=await libPatch('/api/settings',{revision:settingsState.data.settings.revision,...body});
  if(!state.job)applySavedDefaults();aiFillSelectors();aiRender();renderComputeSettings();toast('已保存默认设置，用于后续新任务。');
}
$('app-settings-button').addEventListener('click',handle(openAppSettings));
$('app-settings-form').addEventListener('submit',handle(async event=>{
  event.preventDefault();const models={},trust={};for(const key in settingsState.data.functions)models[key]=$('setting-model-'+key).value;for(const key in trustNames)trust[key]=$('trust-'+key).value;
  $('settings-save').disabled=true;
  try{await saveDefaults({models,trust,review_enabled:$('default-review').checked,ocr_enabled:$('default-ocr').checked,review_policy:$('default-review-policy').value});$('settings-save-state').textContent='已保存，下次新任务自动使用。';}finally{$('settings-save').disabled=false;}
}));
$('save-workspace-defaults').addEventListener('click',handle(async()=>{
  await refreshAppSettings();const config=settingsState.data.settings;
  await saveDefaults({review_enabled:$('review-enabled').checked,ocr_enabled:$('ocr-enabled').checked,review_policy:config.review_policy,trust:config.trust,models:{...config.models,speech:$('model').value.startsWith('provider:')?$('model').value:'local:'+$('model').value,review:$('review-model').value,vision:$('vision-model').value}});
}));
for(const id of ['review-enabled','ocr-enabled','review-model','vision-model'])$(id).addEventListener('change',renderComputeSettings);
for(const id of ['default-review','default-review-policy'])$(id).addEventListener('change',renderTrustEnabled);
document.querySelectorAll('[data-settings-tab]').forEach(button=>button.addEventListener('click',()=>settingsPage(button.dataset.settingsTab)));
$('prompt-purpose').addEventListener('change',()=>renderTemplateList());$('prompt-template').addEventListener('change',renderTemplate);
async function savePrompt(copy){
  if(settingsState.busy)return;settingsState.busy=true;
  try{const purpose=$('prompt-purpose').value;settingsState.data=await post('/api/settings/templates',{revision:settingsState.data.settings.revision,purpose,id:copy?null:settingsState.template.id,name:$('prompt-name').value,text:$('prompt-text').value,make_default:true});renderTemplateList();$('prompt-save-state').textContent='模板已保存并设为默认。';toast('提示词模板已保存。');}finally{settingsState.busy=false;}
}
$('prompt-save-copy').addEventListener('click',handle(()=>savePrompt(true)));$('prompt-save').addEventListener('click',handle(()=>savePrompt(false)));
async function activatePrompt(identity){
  if(settingsState.busy)return;settingsState.busy=true;
  try{settingsState.data=await libPatch('/api/settings/templates/active',{revision:settingsState.data.settings.revision,purpose:$('prompt-purpose').value,id:identity});renderTemplateList();$('prompt-save-state').textContent='默认模板已更新。';}finally{settingsState.busy=false;}
}
$('prompt-activate').addEventListener('click',handle(()=>activatePrompt($('prompt-template').value)));$('prompt-restore').addEventListener('click',handle(()=>activatePrompt('builtin')));
refreshAppSettings(true).catch(error=>toast(error.message));
