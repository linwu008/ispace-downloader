'use strict';
let locationRequest=0, locationEvent=null;
function renderTaskEvents(events){
  const body=$('events');body.replaceChildren();
  if(!events.length){const row=el('tr'),cell=el('td','还没有任务记录','empty-row');cell.colSpan=6;row.append(cell);body.append(row);return;}
  for(const event of events){
    const row=el('tr'),file=el('td'),course=el('td',event.course_name||'未知课程'),group=el('td'),status=el('td'),time=el('td',formatTime(event.created)),actions=el('td');
    file.append(el('strong',event.name),el('div',event.message,'task-detail'));
    group.append(el('div',event.group_title||'未关联分组'));
    if(event.group_folder)group.append(el('div',event.group_folder,'task-detail'));
    status.append(el('span',labels[event.status]||event.status,'badge '+(event.status==='failed'?'failed':event.status==='downloaded'?'success':'neutral')));
    const button=el('button','查看位置','secondary');button.onclick=()=>showEventLocation(event.id);actions.append(button);
    [file,course,group,status,time,actions].forEach((cell,index)=>cell.dataset.label=['文件与详情','所属课程','分组 / 目录','状态','时间','操作'][index]);
    row.append(file,course,group,status,time,actions);body.append(row);
  }
}
async function showEventLocation(eventId){
  const request=++locationRequest;locationEvent=eventId;
  $('location-fields').replaceChildren();$('location-note').textContent='正在核对文件位置…';$('locate-task-file').disabled=true;
  if(!$('task-location-dialog').open)$('task-location-dialog').showModal();
  try{
    const result=await api(`/events/${eventId}/location`);
    if(request!==locationRequest)return;
    $('location-note').textContent=result.note;
    const fields=[['课程',result.course_name],['任务分组',result.group_title],['文件',result.name]];
    if(result.current_group_title&&result.current_group_title!==result.group_title)fields.push(['当前分组',result.current_group_title]);
    if(result.folder)fields.push(['当前所在文件夹',result.folder]);
    if(result.path)fields.push(['完整文件路径',result.path]);
    if(result.recorded_path&&result.recorded_path!==result.path)fields.push(['记录时保存位置',result.recorded_path]);
    for(const [label,value] of fields){
      const block=el('div',undefined,'location-field');block.append(el('dt',label),el('dd',value||'—'));$('location-fields').append(block);
    }
    $('locate-task-file').disabled=!result.exists;
  }catch(error){if(request===locationRequest)$('location-note').textContent=error.message;}
}
$('locate-task-file').onclick=async()=>{
  try{await api(`/events/${locationEvent}/locate`,'POST');$('location-note').textContent='已在资源管理器中定位该文件。';}
  catch(error){$('location-note').textContent=error.message;}
};
$('task-location-dialog').addEventListener('close',()=>{locationRequest++;locationEvent=null;});
