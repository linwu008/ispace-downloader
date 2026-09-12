'use strict';
let workspaceState=null, courseView=null, previewItem=null, courseLoad=0, observedOperation='', addChoices=new Set();
const pageNames={overview:'总览',courses:'我的课程',library:'资料库',history:'任务记录',settings:'设置'};
function routePage(){
  const key=location.hash.replace(/^#\/?/,'')||'overview',page=pageNames[key]?key:'overview';
  document.querySelectorAll('.page-view').forEach(view=>view.hidden=view.id!==page);
  document.querySelectorAll('.nav').forEach(link=>{const selected=link.hash===`#/${page}`;link.classList.toggle('active',selected);if(selected)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');});
  $('page-title').textContent=pageNames[page];document.querySelector('.breadcrumb').textContent='工作空间 / '+pageNames[page];
  document.title=pageNames[page]+' · iSpace v0.3';$('sidebar').classList.remove('menu-open');$('mobile-menu').setAttribute('aria-expanded','false');
  if(page==='library')loadLibrary();
}
window.addEventListener('hashchange',routePage);routePage();
$('mobile-menu').onclick=()=>{$('mobile-menu').setAttribute('aria-expanded',String($('sidebar').classList.toggle('menu-open')));};
document.querySelectorAll('[data-close]').forEach(button=>button.onclick=()=>$(button.dataset.close).close());
function courseNotice(text,error=false){$('course-message').textContent=text;$('course-message').classList.toggle('error',error);}
function fail(error){notice(error.message,true);transientUntil=Date.now()+7000;if($('course-dialog').open)courseNotice(error.message,true);}
function renderCourseCards(courses,groups){
  const signature=JSON.stringify([courses,groups]);if(signature===courseSignature)return;courseSignature=signature;
  const container=$('course-list');container.replaceChildren();
  const added=courses.filter(c=>c.membership==='added');
  if(!added.length){const empty=el('div',undefined,'empty');empty.append(el('h3','选择你正在学习的课程'),el('p','点击“添加课程”，从学校课程列表中选择。'));container.append(empty);}
  for(const course of added){
    const card=el('button',undefined,'course-card');card.type='button';card.dataset.courseId=course.id;card.setAttribute('aria-haspopup','dialog');
    const count=groups.filter(g=>g.course_id===course.id).length;
    card.append(el('span',course.sync_mode==='all'?'整门课自动下载':'仅同步所选文件','course-mode-badge'),el('h3',course.name),el('p',`${count} 个分组 · ${course.downloaded_count||0} / ${course.file_count||0} 项已下载`),el('span',course.folder?(course.enabled?'每日检查已开启':'每日检查未开启'):'下载前需绑定目录','subtle'),el('span','打开课程资料 →','card-link'));
    card.onclick=()=>openCourse(course.id);container.append(card);
  }
}
function renderAvailable(){
  const box=$('available-courses');box.replaceChildren();const query=$('course-search').value.trim().toLowerCase();
  const values=workspaceState.courses.filter(c=>c.membership!=='added'&&c.name.toLowerCase().includes(query));
  for(const course of values){const label=el('label',undefined,'available-course'),check=el('input');check.type='checkbox';check.checked=addChoices.has(course.id);check.onchange=()=>{check.checked?addChoices.add(course.id):addChoices.delete(course.id);};label.append(check,el('span',course.name),el('small',course.membership==='removed'?'可恢复':'','subtle'));box.append(label);}
  if(!values.length)box.append(el('p','没有匹配的课程。可以先关闭窗口，刷新学校列表。','hint'));
}
$('add-course').onclick=()=>{if(!workspaceState)return;addChoices=new Set();$('course-search').value='';renderAvailable();$('add-course-dialog').showModal();};
$('course-search').oninput=renderAvailable;
$('confirm-add').onclick=async()=>{if(!addChoices.size)return;try{await api('/courses/membership','PUT',{course_ids:[...addChoices],added:true});$('add-course-dialog').close();await refresh();}catch(error){fail(error);}};
async function openCourse(id){
  const course=workspaceState.courses.find(c=>c.id===id);if(!course)return;
  let draft=null;try{draft=JSON.parse(sessionStorage.getItem('course-draft-'+id));}catch{}
  courseView={id,items:[],selected:new Set(draft?.ids||[]),mode:draft?.mode||course.sync_mode,dirty:Boolean(draft),initialized:false};
  $('course-dialog-title').textContent=course.name;$('course-folder').value=course.folder||'';$('course-enabled').checked=Boolean(course.enabled)||!course.folder;$('course-mode').value=courseView.mode;
  $('course-file-search').value='';$('course-settings').open=!course.folder;$('group-settings').replaceChildren();$('course-group-list').replaceChildren(el('p','正在读取资料…','hint'));
  courseNotice(course.catalog_error|| (course.catalog_at?'上次清单检查：'+formatTime(course.catalog_at):'首次打开将只读取文件清单，不下载附件。'),Boolean(course.catalog_error));
  if(!$('course-dialog').open)$('course-dialog').showModal();
  await loadCourseFiles();
  if(!course.catalog_at&&!busy)await refreshCatalog();
}
async function loadCourseFiles(){
  if(!courseView)return;const view=courseView,request=++courseLoad;let items=[],page=1;
  try{
    while(true){const result=await api(`/materials?course_id=${view.id}&page=${page}&page_size=100`);items.push(...result.items);if(items.length>=result.total)break;page++;}
    if(courseView!==view||request!==courseLoad)return;
    const course=workspaceState.courses.find(c=>c.id===view.id);view.items=items;
    if(!view.dirty){view.mode=course.sync_mode;view.selected=new Set(items.filter(i=>view.mode==='all'||i.selected).map(i=>i.id));$('course-mode').value=view.mode;}
    view.initialized=true;
    renderCourseGroups();
    if(!$('group-settings').contains(document.activeElement)){
      const previous=$('group-settings').querySelector('details');const wasOpen=previous?.open;
      const details=renderTeachingGroups(course,workspaceState.groups.filter(g=>g.course_id===view.id));details.open=Boolean(wasOpen);$('group-settings').replaceChildren(details);
    }
    courseNotice(course.catalog_error||(course.catalog_at?'清单已更新 · '+formatTime(course.catalog_at):'尚未读取学校文件清单'),Boolean(course.catalog_error));
  }catch(error){fail(error);}
}
function storeDraft(){if(courseView)sessionStorage.setItem('course-draft-'+courseView.id,JSON.stringify({ids:[...courseView.selected],mode:courseView.mode}));}
function updateSelection(){
  if(!courseView)return;const size=courseView.selected.size;
  $('selection-count').textContent=`已选 ${size} / ${courseView.items.length} 项`;
  $('selection-note').textContent=courseView.dirty?'有未保存的选择；保存后每日检查按此范围执行。':courseView.mode==='all'?'包含以后新增的资料。':'新发现的文件会等待你选择。';
  $('save-selection').disabled=busy||!courseView.initialized;$('download-selected').disabled=busy||!size;
}
function choose(ids,checked){
  if(courseView.mode==='all'){courseView.mode='selected';$('course-mode').value='selected';}
  for(const id of ids)checked?courseView.selected.add(id):courseView.selected.delete(id);
  courseView.dirty=true;storeDraft();renderCourseGroups();
}
function displayedStatus(item){return workspaceState?.busy&&workspaceState.active_material===item.id?'下载中':({pending:'未下载',downloaded:'已下载',existing:'已下载',pending_organize:'待整理',missing:'本地缺失',failed:'失败'})[item.status]||item.status;}
function renderCourseGroups(){
  if(!courseView)return;
  const container=$('course-group-list'),opened=new Set([...container.querySelectorAll('details[open]')].map(d=>d.dataset.groupId));
  const first=!container.querySelector('details'),query=$('course-file-search').value.trim().toLowerCase();container.replaceChildren();
  for(const group of workspaceState.groups.filter(g=>g.course_id===courseView.id)){
    const all=courseView.items.filter(i=>i.group_id===group.id),items=all.filter(i=>!query||i.name.toLowerCase().includes(query)||group.title.toLowerCase().includes(query));if(!items.length)continue;
    const details=el('details',undefined,'resource-group');details.dataset.groupId=group.id;details.open=Boolean(query)||opened.has(group.id)||first;
    details.append(el('summary',`${group.title} · ${all.length} 项`));
    const tools=el('div',undefined,'group-actions'),select=el('button','全选此组当前文件','text-button'),clear=el('button','取消此组选择','text-button');
    select.onclick=()=>choose(all.map(i=>i.id),true);clear.onclick=()=>choose(all.map(i=>i.id),false);tools.append(select,clear);details.append(tools);
    for(const item of items){
      const row=el('div',undefined,'resource-row'),label=el('label',undefined,'resource-choice'),check=el('input');check.type='checkbox';check.checked=courseView.selected.has(item.id);check.dataset.materialId=item.id;check.onchange=()=>choose([item.id],check.checked);
      label.append(check,el('span',item.name));const status=el('span',displayedStatus(item),'badge '+(item.status==='failed'?'failed':item.exists?'success':'neutral'));status.dataset.statusId=item.id;
      const controls=el('div',undefined,'resource-controls'),preview=el('button','预览','text-button'),locate=el('button','定位','text-button');preview.onclick=()=>openFilePreview(item);locate.disabled=!item.exists;locate.onclick=()=>api(`/materials/${item.id}/locate`,'POST').catch(fail);controls.append(preview,locate);
      row.append(label,status,controls);if(item.error)row.append(el('p',item.error,'resource-error'));details.append(row);
    }
    container.append(details);
  }
  if(!container.childElementCount)container.append(el('p',query?'没有匹配的文件。':'尚无文件清单，点击“刷新文件清单”。','empty'));
  updateSelection();
}
$('course-file-search').oninput=renderCourseGroups;
$('course-mode').onchange=()=>{courseView.mode=$('course-mode').value;courseView.dirty=true;if(courseView.mode==='all')courseView.selected=new Set(courseView.items.map(i=>i.id));storeDraft();renderCourseGroups();};
async function saveSelection(){await api(`/courses/${courseView.id}/selection`,'PUT',{selected_ids:[...courseView.selected],mode:courseView.mode});courseView.dirty=false;sessionStorage.removeItem('course-draft-'+courseView.id);courseNotice('选择已保存。');await refresh();updateSelection();}
$('save-selection').onclick=()=>saveSelection().catch(fail);
$('download-selected').onclick=async()=>{try{await saveSelection();await api('/downloads','POST',{course_id:courseView.id,material_ids:[...courseView.selected]});courseNotice('正在检查并下载所选文件…');await refresh();}catch(error){fail(error);}};
async function refreshCatalog(){try{await api(`/courses/${courseView.id}/catalog`,'POST');courseNotice('正在读取学校文件清单，不下载附件…');await refresh();}catch(error){fail(error);}}
$('refresh-catalog').onclick=refreshCatalog;
$('choose-course-folder').onclick=async()=>{try{const result=await api('/folder','POST');if(result.folder)$('course-folder').value=result.folder;}catch(error){fail(error);}};
$('save-course-folder').onclick=async()=>{try{await api(`/courses/${courseView.id}`,'PUT',{folder:$('course-folder').value.trim(),enabled:$('course-enabled').checked});await refresh();courseNotice('课程目录与每日检查设置已保存。');}catch(error){fail(error);}};
$('remove-course').onclick=async()=>{try{await api('/courses/membership','PUT',{course_ids:[courseView.id],added:false});$('course-dialog').close();courseView=null;await refresh();notice('已移出我的课程，本地文件与历史记录已保留。');transientUntil=Date.now()+6000;}catch(error){fail(error);}};
$('course-dialog').addEventListener('close',()=>{courseLoad++;courseView=null;});
async function openFilePreview(item){
  previewItem=item;$('file-preview-title').textContent=item.name;$('file-preview-body').replaceChildren();$('file-preview-note').textContent='正在准备预览…';$('preview-locate').disabled=!item.exists;
  if(!$('file-preview-dialog').open)$('file-preview-dialog').showModal();
  try{const ready=await api(`/materials/${item.id}/preview`);if(ready.preview){renderFilePreview(ready);return;}await api(`/materials/${item.id}/preview`,'POST');await refresh();}
  catch(error){$('file-preview-note').textContent=error.message;}
}
function renderFilePreview(result){
  const info=result.preview;if(!previewItem||previewItem.id!==info.item_id)return;
  const body=$('file-preview-body');body.replaceChildren();$('file-preview-note').textContent=(info.temporary?'临时预览，未保存到课程目录。':'正在查看本地文件。')+(info.truncated?'文本仅显示前 1 MB。':'');
  if(info.mime==='text/plain'){const pre=el('pre','正在读取文本…');body.append(pre);fetch(info.url).then(async response=>{if(!response.ok)throw new Error('预览已失效，请重新打开');pre.textContent=await response.text();}).catch(error=>pre.textContent=error.message);}
  else if(info.mime.startsWith('image/')){const img=el('img');img.src=info.url;img.alt=previewItem.name;body.append(img);}
  else {const frame=el('iframe');frame.src=info.url;frame.title=previewItem.name+'预览';body.append(frame);}
}
$('preview-locate').onclick=()=>api(`/materials/${previewItem.id}/locate`,'POST').catch(error=>$('file-preview-note').textContent=error.message);
$('preview-download').onclick=async()=>{try{await api('/downloads','POST',{course_id:previewItem.course_id,material_ids:[previewItem.id]});$('file-preview-note').textContent='已提交下载。若要持续跟踪更新，请在课程中勾选并保存。';await refresh();}catch(error){$('file-preview-note').textContent=error.message;}};
$('file-preview-dialog').addEventListener('close',()=>{previewItem=null;$('file-preview-body').replaceChildren();});
window.addEventListener('ispace-state',event=>{
  workspaceState=event.detail;
  for(const id of ['add-course','confirm-add','refresh-catalog','save-course-folder','remove-course','preview-download'])$(id).disabled=workspaceState.busy;
  if(courseView){updateSelection();for(const item of courseView.items){const status=document.querySelector(`[data-status-id="${item.id}"]`);if(status)status.textContent=displayedStatus(item);}}
  const op=workspaceState.operation,signature=JSON.stringify([op.name,op.finished,op.status]);
  if(!workspaceState.busy&&signature!==observedOperation){
    observedOperation=signature;
    if(courseView&&courseView.initialized)loadCourseFiles();
    if(previewItem&&op.name==='preview'){if(op.preview)renderFilePreview(op);else $('file-preview-note').textContent=op.message;}
  }
});

window.addEventListener('ispace-state',event=>{
  const container=$('organization-history');container.replaceChildren();
  const plans=event.detail.organization_history.filter(p=>p.status!=='preview');
  for(const plan of plans){const row=el('article',undefined,'organization-entry');row.append(el('strong','资料整理 · '+formatTime(plan.created)),el('p',plan.result?.message||(event.detail.busy?'正在整理…':'整理已中断，可重新打开方案继续')));row.append(el('div',[...(plan.course_names||[]),...(plan.group_titles||[])].join(' · '),'organization-context'));const button=el('button','查看整理方案','text-button');button.disabled=event.detail.busy;button.onclick=()=>api('/organization/'+plan.id).then(showOrganization).catch(fail);row.append(button);container.append(row);}
  if(!plans.length)container.append(el('p','尚无整理记录。','empty'));
});
