'use strict';
let csrf = '', busy = false, courseSignature = '', scheduleReady = false, transientUntil = 0;
const $ = id => document.getElementById(id);
const labels = {downloaded:'已下载',skipped:'已存在',failed:'失败',success:'检查完成',partial:'部分未完成',running:'正在处理',auth_required:'需要登录',interrupted:'已中断'};
const formatTime = value => value ? new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}) : '—';
function el(tag, text, cls) { const node=document.createElement(tag); if(text!==undefined)node.textContent=text; if(cls)node.className=cls; return node; }
function notice(text, error=false) { $('notice').textContent=text; $('notice').classList.toggle('error',error); }
async function api(path, method='GET', body) {
  const response=await fetch('/api'+path,{method,headers:{'Content-Type':'application/json','X-iSpace-Token':csrf},body:body===undefined?undefined:JSON.stringify(body)});
  const data=await response.json();
  if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'输入无效，请检查后重试');
  return data;
}
async function action(path, method='POST', body) {
  try {
    const result = await api(path,method,body);
    if (!result.accepted) {
      transientUntil = Date.now() + 6000;
      notice(path.startsWith('/courses/') ? '课程目录已保存，可以立即检查。' : path === '/schedule' ? '每日检查计划已保存。' : '本机凭据与登录状态已清除。');
    } else { transientUntil = 0; notice('操作已提交，正在处理…'); }
    await refresh();
  } catch(error) { transientUntil = Date.now() + 6000; notice(error.message,true); }
}
function renderCourses(courses, groups=[]) { renderCourseCards(courses,groups); }
function renderEvents(events) { renderTaskEvents(events); }
async function refresh() {
  try {
    const state=await api('/state');csrf=state.csrf;busy=state.busy;
    for(const id of ['sync','refresh-courses','manual-login','logout'])$(id).disabled=busy;
    $('login-form').querySelector('button[type=submit]').disabled=busy;
    $('sync').textContent=busy?'处理中…':'↻ 立即检查';
    const bound=state.courses.filter(c=>c.membership==='added'&&c.enabled&&c.folder).length;
    $('sync').disabled=busy||bound===0;
    if(!busy&&bound===0)$('sync').textContent='先绑定课程目录';
    $('bound-count').textContent=bound;$('course-count').textContent=state.courses.filter(c=>c.membership==='added').length;
    const latest=state.runs[0];
    $('download-count').textContent=latest?latest.downloaded:'—';$('failed-count').textContent=latest?latest.failed:'—';
    $('last-check').textContent=latest?`${labels[latest.status]||latest.status} · ${formatTime(latest.finished||latest.started)}`:'尚未执行检查';
    $('next-time').textContent=state.schedule.enabled?state.schedule.time:'未开启';
    $('next-date').textContent=state.schedule.enabled?formatTime(state.schedule.next):'在设置页开启每日检查';
    const logged=state.auth==='logged_in';$('auth-badge').textContent=logged?'已连接（最近验证）':state.auth==='auth_required'?'需要重新登录':'未登录';$('auth-badge').className='badge '+(logged?'success':'neutral');
    if(!scheduleReady){$('schedule-time').value=state.schedule.time;$('schedule-enabled').checked=state.schedule.enabled;scheduleReady=true;}
    renderCourses(state.courses,state.groups||[]);renderEvents(state.events);
    window.dispatchEvent(new CustomEvent('ispace-state',{detail:state}));
    const op=state.operation;
    if(busy)notice(op.name==='manual_login'?'请在弹出的浏览器中完成登录，窗口最多等待 3 分钟。':'正在处理，请保持电脑联网。文件多时首次核对会需要一些时间。');
    else if(Date.now()<transientUntil){}
    else if(op.status)notice(op.message||(op.status==='success'?'检查完成，资料已归位。':'部分项目尚未完成，请查看同步记录并重试。'),['failed','partial','auth_required','interrupted'].includes(op.status));
    else notice(logged?'账号已连接。刷新课程，为需要同步的课程绑定目录。':'欢迎。先连接学校账号，再为课程选择本地文件夹。');
  } catch(error) {notice('无法连接本机服务：'+error.message,true);}
}
$('login-form').addEventListener('submit',event=>{event.preventDefault();const password=$('password').value;$('password').value='';action('/login','POST',{username:$('username').value.trim(),password});});
$('manual-login').onclick=()=>action('/login/manual');
$('logout').onclick=()=>action('/logout');
$('sync').onclick=()=>action('/sync');
$('refresh-courses').onclick=()=>action('/courses/refresh');
$('schedule-form').addEventListener('submit',event=>{event.preventDefault();action('/schedule','PUT',{time:$('schedule-time').value,enabled:$('schedule-enabled').checked});});
window.addEventListener('DOMContentLoaded',()=>{refresh();setInterval(()=>{if(!document.hidden)refresh();},4000);});
