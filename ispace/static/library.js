'use strict';
let libraryState = null, libraryPage = 1, libraryRequest = 0, libraryOptions = '', activePreview = null;
const materialLabels = {pending:'等待检查',downloaded:'已下载',existing:'已存在',pending_organize:'待整理',failed:'检查失败',missing:'本地缺失'};
const actionLabels = {settings:'设置',move:'整理',copy:'跨组复制',reuse:'复用',skip:'跳过'};

function optionList(select, values, first) {
  const previous=select.value;
  select.replaceChildren(new Option(first,''));
  values.forEach(([value,label])=>select.add(new Option(label,String(value))));
  if([...select.options].some(o=>o.value===previous))select.value=previous;
}
function updateLibraryFilters() {
  if(!libraryState)return;
  const signature=JSON.stringify([libraryState.courses.map(c=>[c.id,c.name]),libraryState.groups.map(g=>[g.id,g.course_id,g.title]),$('filter-course').value]);
  if(signature===libraryOptions)return;
  libraryOptions=signature;
  optionList($('filter-course'),libraryState.courses.map(c=>[c.id,c.name]),'所有课程');
  const course=$('filter-course').value;
  optionList($('filter-group'),libraryState.groups.filter(g=>!course||String(g.course_id)===course).map(g=>[g.id,g.title]),'所有分组');
}
async function loadLibrary() {
  if(!libraryState)return;
  const request=++libraryRequest;
  const query=new URLSearchParams({q:$('file-search').value,page:libraryPage,page_size:20});
  for(const [id,key] of [['filter-course','course_id'],['filter-group','group_id'],['filter-status','status']])if($(id).value)query.set(key,$(id).value);
  try {
    const data=await api('/materials?'+query);
    if(request!==libraryRequest)return;
    $('material-total').textContent=data.total;
    $('page-number').textContent=`第 ${data.page} 页 / 共 ${Math.max(1,Math.ceil(data.total/data.page_size))} 页`;
    $('previous-page').disabled=data.page<=1;$('next-page').disabled=data.page*data.page_size>=data.total;
    const body=$('material-list');body.replaceChildren();
    if(!data.items.length){const row=el('tr'),cell=el('td','没有符合条件的资料。同步检查后会建立分组索引。','empty-row');cell.colSpan=4;row.append(cell);body.append(row);}
    for(const item of data.items){
      const row=el('tr'),name=el('td'),context=el('td'),status=el('td'),actions=el('td');
      name.append(el('strong',item.name),el('div',item.path||'尚未保存到本机','file-path'));
      context.append(el('div',item.group_title),el('small',item.course_name,'subtle'));
      status.append(el('span',materialLabels[item.status]||item.status,'badge '+(item.status==='failed'||item.status==='missing'?'failed':item.status==='pending_organize'?'pending':'success')));
      if(item.error)status.append(el('div',item.error,'file-path'));
      const locate=el('button','定位文件','text-button');locate.disabled=!item.exists;
      locate.onclick=async()=>{try{await api(`/materials/${item.id}/locate`,'POST');}catch(error){notice(error.message,true);transientUntil=Date.now()+6000;}};
      const select=el('select');select.setAttribute('aria-label',`调整 ${item.name} 的分组`);
      libraryState.groups.filter(g=>g.course_id===item.course_id).forEach(g=>select.add(new Option(g.title,g.id)));select.value=item.group_id;
      const adjust=el('button','预览调整','text-button');adjust.onclick=async()=>{try{showOrganization(await api(`/materials/${item.id}/group`,'PUT',{group_id:select.value}));}catch(error){notice(error.message,true);transientUntil=Date.now()+6000;}};
      const placement=el('div',undefined,'placement-control');placement.append(select,adjust);actions.append(locate,placement);
      row.append(name,context,status,actions);body.append(row);
    }
  } catch(error){notice(error.message,true);transientUntil=Date.now()+6000;}
}
function renderTeachingGroups(course, groups) {
  const details=el('details',undefined,'teaching-groups');details.dataset.courseId=course.id;
  const summary=el('summary',`${groups.length} 个教学分组 · ${groups.reduce((n,g)=>n+g.pending_count,0)} 项待整理`);
  details.append(summary);
  if(!groups.length){details.append(el('p','点击“立即检查”读取章节和资料分组。旧资料会先等待整理确认。','hint'));return details;}
  for(const group of groups){
    const row=el('div',undefined,'teaching-group'),heading=el('div',undefined,'group-heading');
    heading.append(el('strong',group.title),el('span',`${group.file_count} 项 · ${group.mode==='manual'?'手动目录':'自动目录'}`,'subtle'));
    const line=el('div',undefined,'folder-row'),input=el('input');input.value=group.folder;input.setAttribute('aria-label',group.title+'的子文件夹');
    const choose=el('button','选择子目录','secondary');choose.onclick=async()=>{try{const result=await api('/folder','POST');if(!result.folder)return;const root=course.folder.replaceAll('\\','/').replace(/\/$/,'');const chosen=result.folder.replaceAll('\\','/');if(!chosen.toLowerCase().startsWith(root.toLowerCase()+'/'))throw new Error('请选择这门课程目录内的子文件夹');input.value=chosen.slice(root.length+1);}catch(error){notice(error.message,true);transientUntil=Date.now()+6000;}};
    line.append(input,choose);
    const buttons=el('div',undefined,'group-actions'),save=el('button','预览目录修改','text-button'),reset=el('button','恢复自动目录','text-button'),browse=el('button','查看资料','text-button');
    save.onclick=async()=>{try{showOrganization(await api(`/groups/${group.id}`,'PUT',{mode:'manual',folder:input.value.trim()}));}catch(error){notice(error.message,true);transientUntil=Date.now()+6000;}};
    reset.onclick=async()=>{try{showOrganization(await api(`/groups/${group.id}`,'PUT',{mode:'auto'}));}catch(error){notice(error.message,true);transientUntil=Date.now()+6000;}};
    browse.onclick=()=>{$('filter-course').value=String(course.id);libraryOptions='';updateLibraryFilters();$('filter-group').value=group.id;libraryPage=1;loadLibrary();$('library').scrollIntoView({behavior:'smooth'});};
    buttons.append(save,reset,browse);row.append(heading,line,buttons);details.append(row);
  }
  return details;
}
function showOrganization(plan) {
  activePreview=plan;
  $('preview-description').textContent='核对原位置与目标位置。只有勾选并确认的项目才会执行；冲突和内容已修改的文件保留原状。';
  const body=$('preview-rows');body.replaceChildren();
  for(const item of plan.rows){
    const row=el('tr'),check=el('td'),checkbox=el('input');checkbox.type='checkbox';checkbox.className='preview-choice';checkbox.dataset.id=item.id;checkbox.checked=item.selectable;checkbox.disabled=!item.selectable||item.id==='settings';checkbox.setAttribute('aria-label',`选择 ${item.name}`);check.append(checkbox);
    const name=el('td');name.append(el('strong',item.name),el('div',actionLabels[item.action]||item.action,'subtle'));
    row.append(check,name,el('td',item.source||'—','file-path'),el('td',item.target||'—','file-path'),el('td',item.reason));body.append(row);
  }
  $('execute-organization').disabled=busy||!plan.rows.some(r=>r.selectable);
  $('preview-empty').hidden=plan.rows.length>0;
  if(!$('organization-dialog').open)$('organization-dialog').showModal();
}
window.showOrganization=showOrganization;
$('preview-organization').onclick=async()=>{try{showOrganization(await api('/organization/preview','POST',{course_id:$('filter-course').value?Number($('filter-course').value):null,group_id:$('filter-group').value||null}));}catch(error){notice(error.message,true);transientUntil=Date.now()+6000;}};
$('close-preview').onclick=()=>$('organization-dialog').close();
$('select-preview').onclick=()=>document.querySelectorAll('.preview-choice:not(:disabled)').forEach(c=>c.checked=true);
$('clear-preview').onclick=()=>document.querySelectorAll('.preview-choice:not(:disabled)').forEach(c=>c.checked=false);
$('execute-organization').onclick=async()=>{
  if(!activePreview)return;
  const selected=[...document.querySelectorAll('.preview-choice:checked')].map(c=>c.dataset.id);
  if(!selected.length){$('preview-description').textContent='请至少选择一个可执行项目。';return;}
  try{await api('/organization/execute','POST',{preview_id:activePreview.id,selected});$('organization-dialog').close();notice('正在执行已确认的整理项目…');await refresh();await loadLibrary();}catch(error){$('preview-description').textContent=error.message;}
};
let searchTimer;
$('file-search').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{libraryPage=1;loadLibrary();},250);});
for(const id of ['filter-course','filter-group','filter-status'])$(id).onchange=()=>{libraryPage=1;if(id==='filter-course'){libraryOptions='';$('filter-group').value='';updateLibraryFilters();}loadLibrary();};
$('previous-page').onclick=()=>{libraryPage=Math.max(1,libraryPage-1);loadLibrary();};
$('next-page').onclick=()=>{libraryPage++;loadLibrary();};
const resumeOrganization=el('button','继续上次整理','text-button');
$('organization-result').after(resumeOrganization);
resumeOrganization.hidden=true;
resumeOrganization.onclick=async()=>{try{showOrganization(await api('/organization/'+resumeOrganization.dataset.plan));}catch(error){notice(error.message,true);}};
window.addEventListener('ispace-state',event=>{
  libraryState=event.detail;updateLibraryFilters();
  $('preview-organization').disabled=libraryState.busy;
  $('execute-organization').disabled=libraryState.busy||!activePreview?.rows.some(r=>r.selectable);
  const unfinished=libraryState.organization_history.find(p=>['partial','running'].includes(p.status));
  resumeOrganization.hidden=!unfinished;resumeOrganization.disabled=libraryState.busy;
  if(unfinished)resumeOrganization.dataset.plan=unfinished.id;
  const latest=libraryState.organization_history.find(p=>p.result);
  $('organization-result').textContent=latest?latest.result.message:'整理操作独立记录，尚未执行已确认的整理。';
  if(!libraryState.busy)loadLibrary();
});
