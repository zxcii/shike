"use strict";
const libraryState = {data:{books:[],categories:[],courses:[]}, book:null, category:null, editBook:null, editCategory:null, organize:null, coverURL:null, sortable:null, treeKey:null, shelfKey:null, detailKey:null, savingBook:false, savingCategory:false};
const libEsc = escapeHTML;
const libPatch = (path, body) => api(path,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
function coursePlacement(id) { return libraryState.data.courses.find(c=>c.job_id===id) || {job_id:id,book_id:null,category_id:null,title:'',learned_at:null}; }
function courseTitle(job) { return coursePlacement(job.id).title || job.filename; }
function categoryPath(id) {
  const result=[],seen=new Set();
  while(id && !seen.has(id)){seen.add(id);const node=libraryState.data.categories.find(c=>c.id===id);if(!node)break;result.unshift(node.name);id=node.parent_id;}
  return result;
}
function courseLocation(id) {const p=coursePlacement(id), book=libraryState.data.books.find(b=>b.id===p.book_id);return book?[book.name,...categoryPath(p.category_id)].join(' / '):'待整理课程';}
function categoryOptions(bookId, selected='', exclude=null) {
  const excluded=new Set(exclude?[exclude]:[]);let expanded=true;
  while(expanded){expanded=false;for(const c of libraryState.data.categories){if(excluded.has(c.parent_id)&&!excluded.has(c.id)){excluded.add(c.id);expanded=true;}}}
  const build=(parent=null,depth=0)=>libraryState.data.categories.filter(c=>c.book_id===bookId && c.parent_id===parent && !excluded.has(c.id)).map(c=>`<option value="${libEsc(c.id)}" ${c.id===selected?'selected':''}>${'　'.repeat(depth)}${depth?'↳ ':''}${libEsc(c.name)}</option>`+build(c.id,depth+1)).join('');
  return '<option value="">书本根目录</option>'+build();
}
function bookOptions(selected='') {return '<option value="">待整理课程</option>'+libraryState.data.books.map(b=>`<option value="${libEsc(b.id)}" ${b.id===selected?'selected':''}>${libEsc(b.name)}</option>`).join('');}
function refreshUploadLocation(prefer=false) {
  const existing=state.job && !state.file ? coursePlacement(state.job.id) : null;
  const selected=existing?existing.book_id:prefer?(libraryState.book==='unfiled'?null:libraryState.book):$('upload-book').value;
  const category=existing?existing.category_id:prefer?libraryState.category:$('upload-category').value;
  $('upload-book').innerHTML=bookOptions(selected);
  $('upload-category').innerHTML=categoryOptions(selected,category);
  $('upload-book').disabled=Boolean(existing)||state.uploading;
  $('upload-category').disabled=Boolean(existing)||!selected||state.uploading;
}
async function libraryRefresh() {
  libraryState.data=await api('/api/library');libraryState.shelfKey=null;
  if(libraryState.book && libraryState.book!=='unfiled' && !libraryState.data.books.some(b=>b.id===libraryState.book))libraryState.book=null;
  refreshUploadLocation();renderShelf();if(typeof renderLearning==='function')renderLearning();
}
function bookJobs(bookId) {return state.jobs.filter(j=>j.kind==='transcribe' && (coursePlacement(j.id).book_id || 'unfiled')===bookId);}
function quizForCourse(jobId) {return typeof learningState==='undefined'?[]:learningState.allQuizzes.filter(q=>q.source_job_id===jobId);}
function bookCard(book) {
  const jobs=bookJobs(book.id), learned=jobs.filter(j=>coursePlacement(j.id).learned_at).length;
  const quizCount=jobs.reduce((n,j)=>n+quizForCourse(j.id).length,0);
  return `<article class="shelf-book" data-id="${libEsc(book.id)}"><button class="book-cover cover-${libEsc(book.theme)} ${book.cover?'has-image':''}" data-book-open="${libEsc(book.id)}" aria-label="打开书本：${libEsc(book.name)}">${book.cover?`<img src="/api/library/books/${encodeURIComponent(book.id)}/cover?v=${encodeURIComponent(book.updated_at)}" alt="${libEsc(book.name)}的封面">`:`<span class="book-edition">SHIKE / LEARNING COLLECTION</span><span class="book-cover-title">${libEsc(book.name)}</span><span class="book-cover-rule"></span><span class="book-cover-footer">${jobs.length} LESSONS<br>把知识，读成自己的</span>`}<span class="book-spine"></span></button><div class="shelf-book-caption"><button data-book-open="${libEsc(book.id)}" class="book-name">${libEsc(book.name)}</button><button class="icon-button book-drag-handle" aria-label="拖动排序：${libEsc(book.name)}" title="拖动排序">⠿</button></div><p>${jobs.length} 节课程 · ${learned} 节已学 · ${quizCount} 份练习</p><div class="book-caption-actions"><button class="text-button" data-book-edit="${libEsc(book.id)}">名称与封面</button><button class="text-button" data-book-shift="${libEsc(book.id)}" data-direction="-1" aria-label="前移书本：${libEsc(book.name)}">←</button><button class="text-button" data-book-shift="${libEsc(book.id)}" data-direction="1" aria-label="后移书本：${libEsc(book.name)}">→</button></div></article>`;
}
function renderShelf() {
  if(state.view!=='history') return;
  const query=$('library-search').value.trim().toLowerCase();
  const jobs=state.jobs.filter(j=>j.kind==='transcribe');
  $('library-overview').textContent=`${libraryState.data.books.length} 本书 · ${jobs.length} 节课程`;
  $('bookshelf-grid').hidden=Boolean(libraryState.book);$('book-detail').hidden=!libraryState.book;
  if(!libraryState.book){
    const books=libraryState.data.books.filter(b=>!query||[b.name,b.description,...bookJobs(b.id).map(courseTitle)].join(' ').toLowerCase().includes(query));
    const unfiled=bookJobs('unfiled').filter(j=>!query||[courseTitle(j),j.filename].join(' ').toLowerCase().includes(query));
    const key=JSON.stringify([books,state.jobs,libraryState.data.courses,typeof learningState!=='undefined'?learningState.allQuizzes:[],query]);
    if(key!==libraryState.shelfKey){
      libraryState.sortable?.destroy();libraryState.shelfKey=key;
      $('bookshelf-grid').innerHTML=books.map(bookCard).join('')+`<article class="shelf-book unfiled-book"><button class="book-cover cover-paper" data-book-open="unfiled" aria-label="打开待整理课程"><span class="book-edition">INBOX / 待整理</span><span class="book-cover-title">待整理<br>课程</span><span class="book-cover-rule"></span><span class="book-cover-footer">${unfiled.length} LESSONS<br>先收下，再慢慢整理</span><span class="book-spine"></span></button><p>${unfiled.length} 节未归类课程</p></article><button class="shelf-add-book" id="shelf-add-book"><span>＋</span>新建一本学习书</button>`;
      libraryState.sortable=Sortable.create($('bookshelf-grid'),{animation:160,handle:'.book-drag-handle',draggable:'.shelf-book[data-id]',disabled:Boolean(query),onEnd:()=>{
        const ids=Array.from($('bookshelf-grid').querySelectorAll('[data-id]'),el=>el.dataset.id);
        libPatch('/api/library/books/order',{ids}).then(libraryRefresh).catch(error=>{toast(error.message);libraryRefresh().catch(()=>{});});
      }});
    }
  }else renderBookDetail();
  const downloads=state.jobs.filter(j=>j.kind==='download');$('download-history').hidden=!downloads.length;
  $('download-history-list').innerHTML=downloads.map(j=>`<div class="download-row"><button class="text-button" data-open="${j.id}">${libEsc(j.filename)}</button><span class="status-tag ${statusClass(j.status)}">${libEsc(labels[j.status]||j.status)}</span></div>`).join('');
}
function renderBookDetail() {
  const book=libraryState.data.books.find(b=>b.id===libraryState.book), unfiled=libraryState.book==='unfiled';
  $('book-detail-title').textContent=book?.name||'待整理课程';$('book-detail-description').textContent=book?.description || (unfiled?'将课程归入一本书，逐步建立自己的知识目录。':'书内按章节组织，每一课都有学习、练习与分析。');
  $('book-directory').hidden=unfiled;$('book-edit-current').hidden=unfiled;
  const category=libraryState.data.categories.find(c=>c.id===libraryState.category);
  $('category-current-title').textContent=category?categoryPath(category.id).join(' / '):'全部课程';
  $('category-edit').disabled=!category;$('category-delete').disabled=!category;
  const selected=new Set(libraryState.category?[libraryState.category]:[]);
  if($('include-descendants').checked && selected.size){let changed=true;while(changed){changed=false;for(const c of libraryState.data.categories)if(selected.has(c.parent_id)&&!selected.has(c.id)){selected.add(c.id);changed=true;}}}
  const query=$('library-search').value.trim().toLowerCase();
  const jobs=bookJobs(libraryState.book).filter(j=>(!libraryState.category||selected.has(coursePlacement(j.id).category_id))&&(!query||[courseTitle(j),j.filename,...categoryPath(coursePlacement(j.id).category_id)].join(' ').toLowerCase().includes(query)));
  const detailKey=JSON.stringify([libraryState.book,libraryState.category,jobs,libraryState.data.courses,typeof learningState==='undefined'?[]:learningState.allQuizzes]);
  if(detailKey===libraryState.detailKey){if(book)buildCategoryTree(book);return;}
  libraryState.detailKey=detailKey;
  $('history-list').innerHTML=jobs.length?jobs.map(j=>{
    const quizzes=quizForCourse(j.id), submitted=quizzes.filter(q=>q.submitted).length, analysed=quizzes.filter(q=>q.has_analysis).length;
    const date=new Date(j.created_at).toLocaleDateString('zh-CN');
    return `<article class="course-record"><div class="course-record-main"><div class="course-record-top"><span class="course-record-location">${libEsc(categoryPath(coursePlacement(j.id).category_id).join(' / ') || '书本根目录')}</span><span class="status-tag ${statusClass(j.status)}">${libEsc(activeStatuses.has(j.status)&&j.throttle_state==='cooling'?'冷却中':activeStatuses.has(j.status)&&j.throttle_state==='thermal_wait'?'等待降温':labels[j.status]||j.status)}</span></div><button class="course-record-title" data-open="${j.id}">${libEsc(courseTitle(j))}</button><p>${date}${j.duration?' · '+timecode(j.duration):''} · ${coursePlacement(j.id).learned_at?'已标记学习':'尚未标记学习'} · ${quizzes.length} 份题集</p><div class="course-record-stages"><span class="${j.status==='completed'?'done':''}">① 学习</span><span class="${quizzes.length?'done':''}">② 出题</span><span class="${submitted?'done':''}">③ 答题</span><span class="${analysed?'done':''}">④ 分析</span></div></div><div class="course-record-actions"><button class="button secondary small" data-open="${j.id}">继续学习</button><button class="text-button" data-course-generate="${j.id}" ${j.status!=='completed'?'disabled':''}>出题 / 练习</button><button class="text-button" data-organize="${j.id}">名称与归类</button><button class="icon-button" ${activeStatuses.has(j.status)?'data-cancel':'data-delete'}="${j.id}" aria-label="${activeStatuses.has(j.status)?'取消任务':'删除课程副本'}">${icon(activeStatuses.has(j.status)?'close':'trash')}</button></div></article>`;
  }).join(''):'<div class="history-empty">这里还没有课程。<br>导入新课程，或将待整理课程归入这个目录。</div>';
  if(book)buildCategoryTree(book);
}
function buildCategoryTree(book) {
  const categories=libraryState.data.categories.filter(c=>c.book_id===book.id);
  const key=JSON.stringify([book.id,book.name,categories]);if(libraryState.treeKey===key)return;
  libraryState.treeKey=key;
  const target=jQuery('#category-tree');target.off('.jstree');if(target.jstree(true))target.jstree('destroy');
  target.on('select_node.jstree',(event,data)=>{libraryState.category=data.node.id==='book-root'?null:data.node.id;renderBookDetail();})
    .on('move_node.jstree',(event,data)=>{
      const node=libraryState.data.categories.find(c=>c.id===data.node.id);if(!node)return;
      libPatch(`/api/library/categories/${node.id}`,{name:node.name,parent_id:data.parent==='book-root'?null:data.parent,position:data.position}).then(libraryRefresh).catch(error=>{toast(error.message);libraryState.treeKey=null;libraryRefresh().catch(()=>{});});
    }).jstree({core:{force_text:true,multiple:false,themes:{dots:false,icons:true,responsive:true},check_callback:(operation,node,parent)=>operation!=='move_node'||(node.id!=='book-root' && parent.id!=='#'),data:[{id:'book-root',parent:'#',text:'全部课程',state:{opened:true,selected:!libraryState.category}},...categories.map(c=>({id:c.id,parent:c.parent_id||'book-root',text:c.name,state:{opened:true,selected:c.id===libraryState.category}}))]},plugins:['dnd','search','wholerow'],dnd:{copy:false},search:{show_only_matches:true}});
}
async function openBook(id) {libraryState.book=id;libraryState.category=null;libraryState.treeKey=null;libraryState.shelfKey=null;setView('history');await libraryRefresh();}
function editBook(id=null) {
  libraryState.editBook=id;const book=libraryState.data.books.find(b=>b.id===id);
  $('book-dialog-title').textContent=book?'编辑书本与封面':'新建书本';$('book-name').value=book?.name||'';$('book-description').value=book?.description||'';$('book-theme').value=book?.theme||'sage';$('book-cover-file').value='';$('book-clear-cover').checked=false;$('book-error').textContent='';
  if(libraryState.coverURL)URL.revokeObjectURL(libraryState.coverURL);libraryState.coverURL=null;
  $('book-cover-preview').innerHTML=book?.cover?`<img src="/api/library/books/${encodeURIComponent(book.id)}/cover?v=${encodeURIComponent(book.updated_at)}" alt="当前书本封面">`:'';
  $('book-dialog').showModal();
}
function editCategory(id=null) {
  libraryState.editCategory=id;const category=libraryState.data.categories.find(c=>c.id===id);
  $('category-dialog-title').textContent=category?'编辑分类':'新建子分类';$('category-name').value=category?.name||'';$('category-error').textContent='';
  $('category-parent').innerHTML=categoryOptions(libraryState.book,category?category.parent_id:libraryState.category,id);
  $('category-dialog').showModal();
}
function organizeCourse(id) {
  libraryState.organize=id;const placement=coursePlacement(id),job=state.jobs.find(j=>j.id===id)||state.job;
  $('organize-course-title').value=placement.title||'';$('organize-course-title').placeholder=job?.filename||'课程名称';
  $('organize-course-book').innerHTML=bookOptions(placement.book_id);$('organize-course-category').innerHTML=categoryOptions(placement.book_id,placement.category_id);$('organize-course-learned').checked=Boolean(placement.learned_at);$('course-organize-dialog').showModal();
}
$('book-create').addEventListener('click',()=>editBook());
$('bookshelf-grid').addEventListener('click',handle(async event=>{
  const open=event.target.closest('[data-book-open]'),edit=event.target.closest('[data-book-edit]'),shift=event.target.closest('[data-book-shift]');
  if(open)await openBook(open.dataset.bookOpen);if(edit)editBook(edit.dataset.bookEdit);
  if(event.target.closest('#shelf-add-book'))editBook();
  if(shift){const ids=libraryState.data.books.map(b=>b.id),index=ids.indexOf(shift.dataset.bookShift),other=index+Number(shift.dataset.direction);if(other>=0&&other<ids.length){[ids[index],ids[other]]=[ids[other],ids[index]];await libPatch('/api/library/books/order',{ids});await libraryRefresh();}}
}));
$('book-form').addEventListener('submit',handle(async event=>{
  event.preventDefault();if(libraryState.savingBook)return;libraryState.savingBook=true;$('book-save').disabled=true;$('book-error').textContent='';
  try{const body={name:$('book-name').value,description:$('book-description').value,theme:$('book-theme').value};const book=libraryState.editBook?await libPatch(`/api/library/books/${libraryState.editBook}`,body):await post('/api/library/books',body);libraryState.editBook=book.id;
    const file=$('book-cover-file').files[0];if(file){const form=new FormData();form.append('file',file);await api(`/api/library/books/${book.id}/cover`,{method:'POST',body:form});}else if($('book-clear-cover').checked)await api(`/api/library/books/${book.id}/cover`,{method:'DELETE'});
    $('book-dialog').close();await libraryRefresh();toast('书本已保存。');
  }catch(error){$('book-error').textContent=error.message;throw error;}finally{libraryState.savingBook=false;$('book-save').disabled=false;}
}));
$('book-cover-file').addEventListener('change',()=>{const file=$('book-cover-file').files[0];if(libraryState.coverURL)URL.revokeObjectURL(libraryState.coverURL);libraryState.coverURL=null;if(file){libraryState.coverURL=URL.createObjectURL(file);const img=document.createElement('img');img.src=libraryState.coverURL;img.alt='待保存封面预览';$('book-cover-preview').replaceChildren(img);}});
$('library-back').addEventListener('click',()=>{libraryState.book=null;libraryState.category=null;libraryState.shelfKey=null;renderShelf();});
$('library-refresh').addEventListener('click',handle(async()=>{await refreshJobs();await libraryRefresh();if(typeof loadCourseQuizzes==='function')await loadCourseQuizzes();}));
$('library-search').addEventListener('input',renderShelf);$('include-descendants').addEventListener('change',renderBookDetail);
$('book-edit-current').addEventListener('click',()=>editBook(libraryState.book));
$('book-add-course').addEventListener('click',()=>{newTask();refreshUploadLocation(true);});
$('category-create').addEventListener('click',()=>editCategory());$('category-edit').addEventListener('click',()=>editCategory(libraryState.category));
$('category-search').addEventListener('input',()=>{const tree=jQuery('#category-tree').jstree(true);tree?.search($('category-search').value);});
$('category-form').addEventListener('submit',handle(async event=>{event.preventDefault();if(libraryState.savingCategory)return;libraryState.savingCategory=true;$('category-save').disabled=true;$('category-error').textContent='';
  try{const body={name:$('category-name').value,parent_id:$('category-parent').value||null};const old=libraryState.data.categories.find(c=>c.id===libraryState.editCategory);const category=libraryState.editCategory?await libPatch(`/api/library/categories/${libraryState.editCategory}`,{...body,position:old?.position||0}):await post('/api/library/categories',{...body,book_id:libraryState.book});libraryState.category=category.id;$('category-dialog').close();await libraryRefresh();}
  catch(error){$('category-error').textContent=error.message;throw error;}finally{libraryState.savingCategory=false;$('category-save').disabled=false;}
}));
$('category-delete').addEventListener('click',handle(async()=>{if(!libraryState.category||!confirm('删除这个空分类？有课程或子分类时不会删除。'))return;await api(`/api/library/categories/${libraryState.category}`,{method:'DELETE'});libraryState.category=null;await libraryRefresh();}));
$('history-list').addEventListener('click',handle(async event=>{const organize=event.target.closest('[data-organize]'),generate=event.target.closest('[data-course-generate]');if(organize)organizeCourse(organize.dataset.organize);if(generate){await selectJob(generate.dataset.courseGenerate);if(typeof showCoursePhase==='function')await showCoursePhase('generate');}}));
$('download-history-list').addEventListener('click',handle(async event=>{const button=event.target.closest('[data-open]');if(button)await selectJob(button.dataset.open);}));
$('upload-book').addEventListener('change',()=>{$('upload-category').innerHTML=categoryOptions($('upload-book').value);$('upload-category').disabled=!$('upload-book').value;});
$('organize-course-book').addEventListener('change',()=>{$('organize-course-category').innerHTML=categoryOptions($('organize-course-book').value);});
$('course-organize-form').addEventListener('submit',handle(async event=>{event.preventDefault();$('organize-course-save').disabled=true;try{await libPatch(`/api/library/courses/${libraryState.organize}`,{title:$('organize-course-title').value,book_id:$('organize-course-book').value||null,category_id:$('organize-course-category').value||null,learned:$('organize-course-learned').checked});$('course-organize-dialog').close();await libraryRefresh();toast('课程名称与归类已保存。');}finally{$('organize-course-save').disabled=false;}}));
libraryRefresh().catch(error=>toast(error.message));
