"use strict";
window.CourseNestDemo = (() => {
  let active=sessionStorage.getItem('coursenest-demo')==='1';
  const snapshot={version:'0.6.0',capabilities:['cancel-v1','schedule-v1'],auth:'logged_in',paused:false,at:Math.floor(Date.now()/1000),courses:[{id:1,name:'示例 · 计算机导论',membership:'added',bound:true,enabled:true,folder:'D:\\示例资料\\计算机导论',sync_mode:'selected'},{id:2,name:'示例 · 学术英语',membership:'added',bound:true,enabled:true,folder:'D:\\示例资料\\学术英语',sync_mode:'selected'}],groups:[{id:'week1',course_id:1,title:'Week 1 · Introduction',position:1},{id:'week2',course_id:2,title:'Week 1 · Writing',position:1}],materials:[{id:1,course_id:1,group_id:'week1',name:'Lecture 1.pdf',selected:true,status:'downloaded',path:'D:\\示例资料\\Lecture 1.pdf'},{id:2,course_id:1,group_id:'week1',name:'Practice.docx',selected:false,status:'pending',path:''},{id:3,course_id:2,group_id:'week2',name:'Writing Guide.pdf',selected:true,status:'pending',path:''}]};
  const device={id:'demo-device',name:'示例 Windows 电脑',online:true,last_seen:Math.floor(Date.now()/1000),snapshot,schedule:{enabled:false,time:'20:00'}};
  let jobs=[];
  function enabled(){return active;}
  function enter(){active=true;sessionStorage.setItem('coursenest-demo','1');}
  function exit(){active=false;sessionStorage.removeItem('coursenest-demo');jobs=[];}
  async function request(path,method='GET',body={}){
    if(path==='/health')return {local:false};
    if(path==='/features')return {archive_enabled:false,mail_enabled:false,qa_enabled:false};
    if(path==='/helper')return {version:'0.6.0',url:null,demo:true};
    if(path==='/me')return structuredClone({user:{id:'demo',email:'访客演示'},csrf:'demo-only',device,version:'0.6.0'});
    if(path==='/jobs'&&method==='GET')return structuredClone({items:jobs});
    if(path==='/schedule'){device.schedule={enabled:body.enabled,time:body.time};return {ok:true};}
    if(path==='/auth/logout'){exit();return {ok:true};}
    if(path==='/pairings')return {code:'DEMO-仅供演示',expires:0};
    if(path==='/device'&&method==='DELETE')return {ok:true};
    const cancel=path.match(/^\/jobs\/([^/]+)\/cancel$/);
    if(cancel){const j=jobs.find(x=>x.id===cancel[1]);if(j&&['queued','running'].includes(j.status)){j.status='canceled';j.result={message:'模拟任务已取消，没有操作真实文件'};}return {ok:true};}
    if(path==='/jobs'&&method==='POST'){
      const job={id:crypto.randomUUID(),kind:body.kind,payload:body.payload,created:Math.floor(Date.now()/1000),status:'queued',result:{message:'模拟等待执行，不会连接真实电脑'}};jobs.unshift(job);
      setTimeout(()=>{if(job.status==='queued'){job.status='running';job.result.message='模拟执行中';}},1500);
      setTimeout(()=>{if(job.status!=='running')return;job.status='success';job.result.message='模拟完成，没有下载真实文件';if(job.kind==='selection')for(const f of snapshot.materials)if(f.course_id===body.payload.course_id)f.selected=body.payload.ids.includes(f.id);},10000);
      return {id:job.id};
    }
    throw Error('此操作仅供展示，访客不能使用真实服务。');
  }
  return {enabled,enter,exit,request};
})();

document.addEventListener('click', event => {
  if (!window.CourseNestDemo.enabled()) return;
  const link=event.composedPath().find(n=>n instanceof HTMLAnchorElement);
  if(link && /^http:\/\/(127\.0\.0\.1|localhost):8765(\/|$)/.test(link.href)) {
    event.preventDefault();
    alert('演示模式：仅模拟打开电脑设置，不会连接真实助手。');
  }
},true);
