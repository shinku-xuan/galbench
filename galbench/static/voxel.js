"use strict";

const voxelUI={stages:new Map(),engine:null,loading:null,models:null,current:null,sequence:0,paused:false,ready:false};
function voxelKindFor(row){
  if(!row||["unknown","mixed"].includes(row.model_identity?.source))return "robot";
  const identity=row.model_identity,name=String(identity?.name||row.model||"").toLowerCase().trim().split("/").pop();
  if(/^(?:deepseek)(?:$|[\s._:-]|\d)/.test(name))return "deepseek";
  if(/^claude(?:$|[\s._:-]|\d)/.test(name))return "claude";
  if(/^(?:gpt(?:$|[\s._:-]|\d)|chatgpt(?:$|[-\s])|o[134](?:$|[-.]))/.test(name))return "gpt";
  return "robot";
}
function registerVoxelStage(stage,rows){
  const control={stage,rows,visible:false,abort:new AbortController(),entries:[],previews:false,cancelGestures:[]};
  stage.classList.add("voxel-stage");stage.dataset.voxelMode="pending";
  const toolbar=node("div","voxel-toolbar");
  for(const [action,label,symbol] of [["motion","暂停自动旋转","Ⅱ"],["reset","复位形象朝向","↶"]]){
    const button=node("button","voxel-control",symbol);button.type="button";button.dataset.voxelAction=action;button.title=label;button.setAttribute("aria-label",label);
    button.addEventListener("click",()=>{if(action==="motion"){voxelUI.paused=!voxelUI.paused;try{localStorage.setItem("galbench.voxel.paused",String(voxelUI.paused));}catch(_){}syncVoxelStage();}else if(voxelUI.current===control)voxelUI.engine?.reset();});toolbar.append(button);
  }
  stage.append(toolbar);
  for(const [index,button] of [...stage.querySelectorAll(".podium-person")].entries()){
    const anchor=button.querySelector(".podium-avatar"),entry={anchor,button,kind:voxelKindFor(rows[index]),empty:!rows[index]};control.entries.push(entry);
    anchor.dataset.voxelKind=entry.kind;
    if(rows[index])wireVoxelDrag(control,entry,index);
  }
  voxelUI.stages.set(stage,control);queueMicrotask(()=>{if(stage.isConnected)voxelUI.observer?.observe(stage);});
}
function wireVoxelDrag(control,entry,index){
  const {anchor,button}=entry,options={signal:control.abort.signal};let gesture=null,suppressUntil=0;
  const interactive=()=>entry.available&&voxelUI.current===control&&control.stage.dataset.voxelMode==="interactive";
  anchor.addEventListener("pointerdown",event=>{
    if(!interactive()||event.button!==0||gesture)return;
    gesture={id:event.pointerId,startX:event.clientX,startY:event.clientY,x:event.clientX,y:event.clientY,time:event.timeStamp,moved:false,touch:event.pointerType==="touch"};
    anchor.setPointerCapture(event.pointerId);voxelUI.engine.dragging(index,true);
  },options);
  anchor.addEventListener("pointermove",event=>{
    if(!gesture||gesture.id!==event.pointerId)return;
    const dx=event.clientX-gesture.startX,dy=event.clientY-gesture.startY;
    if(!gesture.moved){if(gesture.touch&&Math.abs(dy)>Math.abs(dx))return;if(Math.hypot(dx,dy)<6)return;gesture.moved=true;anchor.dataset.dragging="true";}
    event.preventDefault();const dt=Math.max(event.timeStamp-gesture.time,8),move=event.clientX-gesture.x;
    voxelUI.engine.drag(index,move,event.clientY-gesture.y,Math.max(-1.5,Math.min(1.5,move/dt*6)));
    gesture.x=event.clientX;gesture.y=event.clientY;gesture.time=event.timeStamp;
  },{...options,passive:false});
  const release=event=>{if(!gesture||gesture.id!==event.pointerId)return;if(gesture.moved)suppressUntil=performance.now()+450;
    gesture=null;delete anchor.dataset.dragging;voxelUI.engine?.dragging(index,false);if(anchor.hasPointerCapture(event.pointerId))anchor.releasePointerCapture(event.pointerId);};
  control.cancelGestures.push(()=>{if(gesture)release({pointerId:gesture.id});});
  for(const type of ["pointerup","pointercancel","lostpointercapture"])anchor.addEventListener(type,release,options);
  button.addEventListener("click",event=>{if(performance.now()<suppressUntil){event.preventDefault();event.stopImmediatePropagation();}}, {...options,capture:true});
  button.addEventListener("keydown",event=>{
    if(!interactive()||!["ArrowLeft","ArrowRight","Home"].includes(event.key))return;
    event.preventDefault();if(event.key==="Home")voxelUI.engine.reset(index);else voxelUI.engine.drag(index,event.key==="ArrowRight"?12:-12,0);
  },options);
}
function initVoxelUI(){
  if(voxelUI.ready)return;voxelUI.ready=true;
  try{voxelUI.paused=localStorage.getItem("galbench.voxel.paused")==="true";}catch(_){}
  voxelUI.observer=new IntersectionObserver(entries=>{for(const e of entries){const c=voxelUI.stages.get(e.target);if(c)c.visible=e.isIntersecting;}syncVoxelStage();},{threshold:.01});
  const mutation=new MutationObserver(()=>{
    for(const [stage,c] of voxelUI.stages)if(!stage.isConnected){c.cancelGestures.forEach(cancel=>cancel());voxelUI.observer.unobserve(stage);voxelUI.resizeObserver.unobserve(stage);c.abort.abort();voxelUI.stages.delete(stage);if(voxelUI.current===c)voxelUI.current=null;}
    syncVoxelStage();
  });mutation.observe($("standings-content"),{childList:true});
  new MutationObserver(syncVoxelStage).observe(document.body,{attributes:true,attributeFilter:["data-view","data-screen"]});
  document.addEventListener("visibilitychange",syncVoxelStage);matchMedia("(prefers-reduced-motion: reduce)").addEventListener("change",syncVoxelStage);
  voxelUI.resizeObserver=new ResizeObserver(()=>voxelUI.engine?.dirty());
  window.addEventListener("resize",()=>voxelUI.engine?.dirty());
  window.addEventListener("pagehide",()=>voxelUI.engine?.setActive(false,false));window.addEventListener("pageshow",syncVoxelStage);
}
async function loadVoxelEngine(){
  if(voxelUI.loading)return voxelUI.loading;
  voxelUI.loading=(async()=>{
    voxelUI.models=await import("/voxel-models.js");
    for(const control of voxelUI.stages.values())paintVoxelPreviews(control);
    if(!["deepseek","gpt","claude","robot"].some(kind=>voxelUI.models.voxelModel(kind)?.faces?.length))return;
    const {createVoxelRenderer}=await import("/voxel-renderer.js");
    voxelUI.engine=createVoxelRenderer(ready=>{if(voxelUI.current)voxelUI.current.stage.dataset.voxelMode=ready?"interactive":"static";if(ready)syncVoxelStage();});
  })().catch(()=>{voxelUI.engine=null;});
  return voxelUI.loading;
}
const voxelPreviewCache=new Map();
function paintVoxelPreviews(control){
  if(!voxelUI.models||control.previews)return;control.previews=true;
  for(const entry of control.entries){
    entry.available=!!voxelUI.models.voxelModel(entry.kind)?.faces?.length;
    entry.anchor.dataset.voxelAvailable=String(entry.available);
    if(!entry.available){entry.anchor.classList.add("voxel-no-model");entry.anchor.setAttribute("role","img");entry.anchor.setAttribute("aria-label","体素形象待制作");continue;}
    if(!voxelPreviewCache.has(entry.kind))voxelPreviewCache.set(entry.kind,"data:image/svg+xml;charset=utf-8,"+encodeURIComponent(voxelUI.models.voxelPreview(entry.kind)));
    const image=node("img","voxel-preview");image.src=voxelPreviewCache.get(entry.kind);image.alt="";image.draggable=false;entry.anchor.append(image);entry.anchor.classList.add("voxel-preview-ready");
  }
}
async function syncVoxelStage(){
  const sequence=++voxelUI.sequence;
  const visible=!document.hidden&&document.body.dataset.screen==="app"&&document.body.dataset.view==="leaderboard";
  const control=visible?[...voxelUI.stages.values()].find(c=>c.stage.isConnected&&c.visible):null;
  if(!control){voxelUI.current?.cancelGestures.forEach(cancel=>cancel());voxelUI.engine?.setActive(false,false);return;}
  await loadVoxelEngine();
  if(sequence!==voxelUI.sequence||!control.stage.isConnected||document.hidden)return;
  paintVoxelPreviews(control);
  if(!voxelUI.engine||!control.entries.some(entry=>entry.available)){voxelUI.engine?.setActive(false,false);control.stage.dataset.voxelMode="static";return;}
  if(voxelUI.current!==control){
    if(voxelUI.current){voxelUI.current.stage.dataset.voxelMode="static";voxelUI.resizeObserver.unobserve(voxelUI.current.stage);}
    voxelUI.current=control;voxelUI.engine.attach(control.stage,control.entries);voxelUI.resizeObserver.observe(control.stage);
  }
  const reduced=matchMedia("(prefers-reduced-motion: reduce)").matches,spin=!voxelUI.paused&&!reduced&&control.rows.some(Boolean);
  const lost=voxelUI.engine.stats().lost;control.stage.dataset.voxelMode=lost?"static":"interactive";
  const motion=control.stage.querySelector('[data-voxel-action="motion"]');motion.textContent=spin?"Ⅱ":"▷";motion.disabled=reduced;
  motion.setAttribute("aria-pressed",String(!spin));motion.setAttribute("aria-label",reduced?"系统已启用减少动态效果":spin?"暂停自动旋转":"继续自动旋转");motion.title=motion.getAttribute("aria-label");
  for(const entry of control.entries)if(!entry.empty){entry.button.title=entry.button.title.replace(/ · 拖动形象.*$/,"")+(lost||!entry.available?"":" · 拖动形象旋转，左右键微调，Home 复位");}
  voxelUI.engine.setActive(true,spin);
}
function voxelDiagnostics(){return {loaded:!!voxelUI.models,stages:voxelUI.stages.size,mode:voxelUI.current?.stage.dataset.voxelMode||"idle",paused:voxelUI.paused,renderer:voxelUI.engine?.stats()||null};}
