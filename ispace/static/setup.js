"use strict";
let token = "", localState = null, editingCourse = null, rendered = "", prompted = false;
const node = id => document.getElementById(id);
async function call(path, method="GET", body) {
  const r=await fetch("/api"+path,{method,headers:{"Content-Type":"application/json","x-ispace-token":token},...(body?{body:JSON.stringify(body)}:{})});
  const v=await r.json();if(!r.ok)throw Error(v.detail||"操作失败");return v;
}
async function act(fn, output="message") {
  try {await fn();} catch(e){node(output).textContent=e.message;}
}
function folderDialog(course=null) {
  editingCourse=course;
  node("folder-kind").value=course?"course":(localState?.folder_setup?.mode||"root");
  node("folder-kind").disabled=!!course;
  node("folder-path").value=course?.folder||localState?.folder_setup?.path||"";
  node("folder-status").textContent="保存前请确认路径；迁移期间不要退出助手。";
  folderFields();
  node("folder-dialog").showModal();
}
async function refresh(){
  const s=await call("/state");localState=s;token=s.csrf;
  const c=await call("/companion");node("connection").textContent=c.message||"请先在官网生成配对码";node("pair").hidden=c.paired;
  node("operation-state").textContent=s.busy?"正在处理任务…":s.auth==="logged_in"?"学校账号已连接":"请连接学校账号";
  node("folder-summary").textContent=s.folder_setup?.mode==="root"?"统一总目录："+s.folder_setup.path:s.folder_setup?.mode==="individual"?"已选择为每门课程单独设置目录":"请选择保存方式：统一总目录或逐课程设置。";
  if(s.folder_warning)node("folder-summary").textContent=s.folder_warning;
  node("plan-status").textContent=s.website_plan?.pending?"正在合并旧计划；如时间冲突，请到官网选择保留的计划。":"每日同步计划统一在官网设置，电脑离线时保留待执行任务。";
  const key=JSON.stringify(s.courses.map(c=>[c.id,c.name,c.folder]));
  if(key!==rendered){rendered=key;node("courses").replaceChildren();
    for(const c of s.courses){const row=document.createElement("p");row.textContent=c.name+" · "+(c.folder||"未设置目录")+" ";const b=document.createElement("button");b.textContent="设置保存位置";b.onclick=()=>folderDialog(c);row.append(b);node("courses").append(row);}
  }
  if(!prompted&&!s.folder_setup?.mode){prompted=true;folderDialog();}
}
function folderFields(){const separate=node("folder-kind").value==="individual";for(const id of ["folder-path","folder-migrate"])node(id).parentElement.hidden=separate;for(const id of ["browse-folder","check-folder"])node(id).hidden=separate;}
node("folder-kind").onchange=folderFields;
node("folder-mode").onclick=()=>folderDialog();
node("close-folder").onclick=()=>node("folder-dialog").close();
node("browse-folder").onclick=()=>act(async()=>{
  const b=node("browse-folder");b.disabled=true;node("folder-status").textContent="请选择弹出的文件夹窗口。没有看到窗口时，可以直接粘贴路径。";
  try{const r=await call("/folder","POST");if(r.folder)node("folder-path").value=r.folder;node("folder-status").textContent=r.folder?"已选择目录，请保存设置。":"未选择目录。";}finally{b.disabled=false;}
},"folder-status");
node("check-folder").onclick=()=>act(async()=>{node("folder-status").textContent="正在检查…";await call("/folders/configure","POST",{mode:"check",folder:node("folder-path").value});node("folder-status").textContent="目录可用。";},"folder-status");
node("folder-form").onsubmit=e=>{e.preventDefault();act(async()=>{
  const submit=e.submitter;submit.disabled=true;node("folder-status").textContent="正在保存，请稍候…";
  try{const r=await call("/folders/configure","POST",{mode:node("folder-kind").value,course_id:editingCourse?.id,folder:node("folder-path").value,migrate:node("folder-migrate").value==="true"});node("folder-dialog").close();node("message").textContent="目录设置成功。"+(r.moved?"已迁移 "+r.moved+" 个文件。":"")+(r.retained?"部分原文件被占用，已保留原件。":"");await refresh();}finally{submit.disabled=false;}
},"folder-status");};
node("pair").onsubmit=e=>{e.preventDefault();act(async()=>{await call("/companion/pair","POST",{server:"https://bnbucoursenest.cn",code:node("code").value,name:"我的 Windows 电脑"});node("code").value="";await refresh();});};
node("login").onclick=()=>act(async()=>{await call("/login/manual","POST");node("message").textContent="请在弹出的学校窗口完成登录。";});
node("refresh").onclick=()=>act(async()=>{await call("/courses/refresh","POST");node("message").textContent="正在读取学校课程…";});
node("disconnect").onclick=()=>act(async()=>{if(confirm("断开这台电脑与官网的连接？本地文件保留。")){await call("/companion/disconnect","POST");await refresh();}});
act(refresh);setInterval(()=>{if(!document.hidden)act(refresh);},5000);
