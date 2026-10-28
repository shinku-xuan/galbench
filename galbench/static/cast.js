"use strict";

// Decorative characters are deliberately independent of model connections.
const CAST_MEMBERS=[
  {id:"deepseek",name:"DeepSeek",ext:"png",color:"#5077d6",face:-7,quote:"准备好了吗？这次一起把正确路线找出来。"},
  {id:"gpt",name:"GPT",ext:"jpg",color:"#8f86bb",face:-2,quote:"这段对白，我想再认真读一遍。"},
  {id:"claude",name:"Claude",ext:"jpg",color:"#c78354",face:0,quote:"先翻开这一页，再看看故事的另一面。"},
  {id:"gemini",name:"Gemini",ext:"jpg",color:"#9b7ede",face:-8,quote:"把线索连成星座，也许答案就在其中。"},
  {id:"glm",name:"GLM",ext:"png",color:"#647b99",face:-7,quote:"所有伏笔，都值得收进这本小册子。"},
  {id:"grok",name:"Grok",ext:"jpg",color:"#9d7b60",face:-15,quote:"来吧，让我看看这道题有多难。"},
  {id:"kimi",name:"Kimi",ext:"png",color:"#7c83b1",face:-6,quote:"这段旋律，会属于谁的故事呢？"},
  {id:"minimax",name:"MiniMax",ext:"jpg",color:"#d4879b",face:-5,quote:"镜头准备好了，轮到你登场啦！"},
  {id:"muse",name:"Muse",ext:"png",color:"#a1a7bd",face:-6,quote:"再读一页……我真的没有在打瞌睡。"},
  {id:"qwen",name:"Qwen",ext:"jpg",color:"#8a86cc",face:-5,quote:"不如从人物之间的关系，慢慢推开这扇门。"},
  {id:"seed",name:"Seed",ext:"jpg",color:"#6f9cce",face:-4,quote:"把这一刻写进新的存档，好不好？"}
];
const CAST_SCENES={
  dashboard:{location:"AI 同好会",ghost:"AFTER SCHOOL",action:"准备登场",roster:"选择今日应援",lead:"deepseek",cg:"club-cg-v1.png",cgTitle:"放学后的约定",cgAlt:"DeepSeek、GPT 与 Claude 在阳光下的文学社团相聚"},
  leaderboard:{location:"星光对战场",ghost:"CLASH OF STORIES",action:"走进排行榜",roster:"为谁亮起星光",lead:"gemini",cg:"arena-duel-cg-v1.png",cgTitle:"星光对战场",cgAlt:"Gemini、GPT 与 Grok 在月光擂台上展开三方魔法对战"},
  bank:{location:"放课后图书室",ghost:"ONCE UPON A TIME",action:"翻开题库",roster:"和谁一起翻页",lead:"claude",cg:"archive-table-cg-v1.png",cgTitle:"故事研究桌",cgAlt:"俯视 Claude、Qwen 与 Kimi 围桌整理书页、人物卡片与剧情地图"}
};
const CAST_PREFERENCE_KEY="galbench.cast.v2";
const castUI={ready:false,scene:"dashboard",view:"dashboard",selected:{dashboard:null,leaderboard:null,bank:null},preview:null,line:0,frame:0,swap:null};
const bloomUI={ready:false,visible:false,frame:0,last:0,time:0,pulseAt:-10,hover:false,energy:0,context:null,ratio:1,tokens:[],observer:null};
const castMember=id=>CAST_MEMBERS.find(member=>member.id===id);
const castPortrait=member=>`/ai-cast/${member.id}.${member.ext}`;
const castIcon=member=>`/ai-cast/icons/${member.id}.svg`;
const castReduceMotion=()=>matchMedia("(prefers-reduced-motion: reduce)").matches;

function castImage(image,member,portrait=true){
  const source=portrait?castPortrait(member):castIcon(member);
  if(image.getAttribute("src")!==source)image.src=source;
  image.alt=portrait?`${member.name} 的社区形象立绘`:"";
}
function makeCastChip(member,dialog=false){
  const button=node("button",dialog?"cast-chip cast-dialog-chip":"cast-chip"),portrait=node("span","cast-chip-portrait"),image=node("img"),logo=node("img","cast-chip-logo");
  button.type="button";button.dataset.cast=member.id;button.style.setProperty("--member-color",member.color);button.style.setProperty("--face-offset",member.face+"px");
  button.setAttribute("aria-label",`${dialog?"查看":"选择应援角色"} ${member.name}`);button.setAttribute("aria-pressed","false");
  image.src=castPortrait(member);image.alt="";image.loading="lazy";image.decoding="async";
  logo.src=castIcon(member);logo.alt="";logo.width=16;logo.height=16;
  portrait.append(image);button.append(portrait,logo,node("span","cast-chip-name",member.name));
  button.addEventListener("click",()=>dialog?openCastDialog(member.id):selectCast(member.id));
  if(!dialog){
    button.addEventListener("pointerenter",()=>{if(matchMedia("(hover:hover) and (pointer:fine)").matches)previewCast(member.id);});
    button.addEventListener("focus",()=>previewCast(member.id));
  }
  return button;
}
function resetCastPointer(){
  cancelAnimationFrame(castUI.frame);castUI.frame=0;
  if(castReduceMotion())castUI.swap?.cancel();
  $("cast-hero").style.setProperty("--pointer-x","0");$("cast-hero").style.setProperty("--pointer-y","0");
}
function previewCast(id){
  const preview=id===castUI.selected[castUI.scene]?null:id;
  if(castUI.preview===preview)return;
  castUI.preview=preview;castUI.line=0;paintCast();
}
function initCastHero(){
  if(castUI.ready)return;castUI.ready=true;
  try{
    let saved=JSON.parse(localStorage.getItem(CAST_PREFERENCE_KEY));
    // The old leaderboard/library defaults were automatically selected cards.
    // Their new scenes open on the CG; keep the user's existing lobby choice.
    if(!saved)saved={dashboard:JSON.parse(localStorage.getItem("galbench.cast.v1"))?.dashboard};
    for(const scene of Object.keys(CAST_SCENES))if(castMember(saved?.[scene])||saved?.[scene]===null)castUI.selected[scene]=saved[scene];
  }catch(_){/* Character preferences are optional. */}
  for(const member of CAST_MEMBERS){$("cast-roster").append(makeCastChip(member));$("cast-dialog-roster").append(makeCastChip(member,true));}
  for(const member of CAST_MEMBERS){
    const token=node("span","bloom-token"),image=node("img");image.src=castIcon(member);image.alt="";image.width=20;image.height=20;
    token.style.setProperty("--brand-glow",member.color+"55");
    token.dataset.cast=member.id;token.append(image);$("cast-bloom-logos").append(token);
  }
  initAIBloom();
  const clearPreview=()=>{if(castUI.preview){castUI.preview=null;castUI.line=0;paintCast();}};
  $("cast-roster").addEventListener("pointerleave",clearPreview);
  $("cast-roster").addEventListener("focusout",event=>{if(!$("cast-roster").contains(event.relatedTarget))clearPreview();});
  $("cast-roster").addEventListener("keydown",event=>{
    if(!["ArrowLeft","ArrowRight","Home","End"].includes(event.key))return;
    const buttons=[...$("cast-roster").querySelectorAll("button")],index=buttons.indexOf(document.activeElement);
    if(index<0)return;event.preventDefault();
    const next=event.key==="Home"?0:event.key==="End"?buttons.length-1:(index+(event.key==="ArrowRight"?1:-1)+buttons.length)%buttons.length;
    buttons[next].focus();buttons[next].scrollIntoView({block:"nearest",inline:"nearest"});
  });
  const currentLead=()=>castUI.preview||castUI.selected[castUI.scene]||CAST_SCENES[castUI.scene].lead;
  $("cast-gallery-open").addEventListener("click",()=>openCastDialog(currentLead()));
  $("cast-main-portrait").addEventListener("click",()=>openCastDialog(currentLead()));
  $("cast-reset-scene").addEventListener("click",()=>selectCast(null));
  $("cast-next-line").addEventListener("click",()=>{castUI.line++;paintCastDialogue();if(!castReduceMotion())$("cast-line").animate([{opacity:.2},{opacity:1}],{duration:260});});
  $("cast-primary").addEventListener("click",()=>{
    if(castUI.scene==="dashboard")openConfig();
    else $(castUI.scene==="bank"?"bank-view":"standings-content").scrollIntoView({behavior:castReduceMotion()?"auto":"smooth",block:"start"});
  });
  $("cast-stage").addEventListener("pointermove",event=>{
    if(castReduceMotion()||event.pointerType==="touch")return;
    const rect=$("cast-stage").getBoundingClientRect(),x=Math.max(-1,Math.min(1,(event.clientX-rect.left)/rect.width*2-1)),y=Math.max(-1,Math.min(1,(event.clientY-rect.top)/rect.height*2-1));
    cancelAnimationFrame(castUI.frame);castUI.frame=requestAnimationFrame(()=>{$("cast-hero").style.setProperty("--pointer-x",x.toFixed(3));$("cast-hero").style.setProperty("--pointer-y",y.toFixed(3));castUI.frame=0;});
  },{passive:true});
  $("cast-stage").addEventListener("pointerleave",resetCastPointer);
  const visibility=()=>{$("cast-bloom").dataset.paused=String(document.hidden);if(document.hidden)resetCastPointer();};
  document.addEventListener("visibilitychange",visibility);visibility();
  matchMedia("(prefers-reduced-motion: reduce)").addEventListener("change",resetCastPointer);
}
function selectCast(id){
  if(id!==null&&!castMember(id))return;
  castUI.selected[castUI.scene]=id;castUI.preview=null;castUI.line=0;
  try{localStorage.setItem(CAST_PREFERENCE_KEY,JSON.stringify(castUI.selected));}catch(_){}
  paintCast();
}
function renderCastHero(view){
  initCastHero();castUI.view=view;castUI.scene=CAST_SCENES[view]?view:"dashboard";castUI.preview=null;castUI.line=0;
  const scene=CAST_SCENES[castUI.scene];$("cast-hero").dataset.scene=castUI.scene;
  const cg=$("cast-cg"),source="/ai-cast/"+scene.cg;
  if(cg.getAttribute("src")!==source)cg.src=source;
  cg.alt=scene.cgAlt;
  $("cast-location").textContent=scene.location;$("cast-ghost-type").textContent=scene.ghost;
  $("cast-roster-heading").textContent=scene.roster;
  $("cast-primary").replaceChildren(document.createTextNode(scene.action),node("span","","↗"));
  resetCastPointer();paintCast();
  if(bloomUI.ready&&!bloomUI.frame)drawAIBloom(bloomUI.time,true);
}
function paintCast(){
  const id=castUI.preview||castUI.selected[castUI.scene],member=castMember(id),show=!!member;
  $("cast-hero").dataset.portrait=String(show);$("cast-main-portrait").hidden=!show;
  $("cast-reset-scene").hidden=!castUI.selected[castUI.scene];
  $("cast-reset-scene").textContent="↶ 回到"+CAST_SCENES[castUI.scene].cgTitle;
  if(member){
    const changed=$("cast-main-image").getAttribute("src")!==castPortrait(member);
    castImage($("cast-main-image"),member);castImage($("cast-main-logo"),member,false);$("cast-main-name").textContent=member.name;
    $("cast-main-portrait").setAttribute("aria-label",`查看 ${member.name} 的完整立绘`);$("cast-hero").style.setProperty("--cast-accent",member.color);
    if(changed&&!castReduceMotion()){castUI.swap?.cancel();castUI.swap=$("cast-main-image").animate([{opacity:.2},{opacity:1}],{duration:280});}
    let friends;
    if(castUI.scene==="bank")friends=["qwen","kimi","claude","glm"].filter(key=>key!==member.id).slice(0,2).map(castMember);
    else{const index=CAST_MEMBERS.indexOf(member);friends=[CAST_MEMBERS[(index+CAST_MEMBERS.length-2)%CAST_MEMBERS.length],CAST_MEMBERS[(index+2)%CAST_MEMBERS.length]];}
    castImage($("cast-companion-left"),friends[0]);castImage($("cast-companion-right"),friends[1]);
  }
  for(const button of $("cast-roster").querySelectorAll("button")){
    const selected=button.dataset.cast===castUI.selected[castUI.scene];button.classList.toggle("is-active",selected);button.classList.toggle("is-preview",button.dataset.cast===castUI.preview);button.setAttribute("aria-pressed",String(selected));
  }
  paintCastDialogue();
}
function paintCastDialogue(){
  const member=castMember(castUI.preview||castUI.selected[castUI.scene]);
  let lines;
  if(member){
    const sceneLine=castUI.scene==="leaderboard"?"应援可以偏心，分数就交给答卷吧。":castUI.scene==="bank"?"别急，故事的伏笔也许就在下一页。":"给这场测试，存下一个新的开始吧。";
    lines=[[member.name,member.quote],[member.name,sceneLine],[member.name,"你来选下一条路线，我会在这里等你。"]];
  }else if(castUI.scene==="leaderboard")lines=[["星光对战场","这场较量，以答卷决胜负。"],["Gemini","每一条攻略线，都有各自的强手。"],["GPT","胜负的悬念，留到成绩揭晓。"],["Grok","来吧，看看谁能记住更多故事！"]];
  else if(castUI.scene==="bank")lines=[["放课后图书室","人物、伏笔和世界，都藏在这本书里。"],["Claude","今天，从哪一页开始读？"],["Qwen","把人物关系理清，故事就慢慢清楚了。"],["Kimi","这段旋律，会属于谁的故事呢？"]];
  else lines=[["攻略同好会","这一次，让模型成为故事里的主角。"],["DeepSeek","新来的同学，来一起研究这份攻略吧！"],["GPT","这场故事，我也想留下一个好结局。"],["Claude","答案或许就藏在某一页，我们一起找找。"]];
  const [name,line]=lines[castUI.line%lines.length];$("cast-speaker").textContent=name;$("cast-line").textContent=`「${line}」`;
}
function openCastDialog(id){
  const member=castMember(id);if(!member)return;
  castImage($("cast-dialog-image"),member);castImage($("cast-dialog-logo"),member,false);
  $("cast-dialog-name").textContent=member.name;$("cast-dialog-quote").textContent=`「${member.quote}」`;
  $("cast-original-link").href=castPortrait(member);
  for(const button of $("cast-dialog-roster").querySelectorAll("button")){const selected=button.dataset.cast===id;button.setAttribute("aria-pressed",String(selected));button.classList.toggle("is-active",selected);}
  if(!$("cast-dialog").open)$("cast-dialog").showModal();
}

// A small, bounded canvas supplies the light, while actual local SVG logos
// stay crisp in compositor-transformed DOM elements. No model calls involved.
function initAIBloom(){
  if(bloomUI.ready)return;bloomUI.ready=true;
  const canvas=$("cast-bloom-canvas");bloomUI.context=canvas.getContext("2d");
  bloomUI.ratio=Math.min(devicePixelRatio||1,2);canvas.width=240*bloomUI.ratio;canvas.height=210*bloomUI.ratio;
  bloomUI.tokens=[...$("cast-bloom-logos").children];
  bloomUI.observer=new IntersectionObserver(entries=>{bloomUI.visible=entries[0].isIntersecting;syncAIBloom();},{threshold:0});
  bloomUI.observer.observe($("cast-bloom"));
  $("cast-bloom-pulse").addEventListener("pointerenter",()=>{bloomUI.hover=true;});
  $("cast-bloom-pulse").addEventListener("pointerleave",()=>{bloomUI.hover=false;});
  $("cast-bloom-pulse").addEventListener("click",()=>{bloomUI.pulseAt=bloomUI.time;if(castReduceMotion())drawAIBloom(bloomUI.time,true);});
  document.addEventListener("visibilitychange",syncAIBloom);
  matchMedia("(prefers-reduced-motion: reduce)").addEventListener("change",syncAIBloom);
  drawAIBloom(0,true);
}
function syncAIBloom(){
  const running=bloomUI.visible&&!document.hidden&&document.body.dataset.screen==="app"&&!castReduceMotion()&&bloomUI.context;
  if(running){if(!bloomUI.frame)bloomUI.frame=requestAnimationFrame(tickAIBloom);}
  else{cancelAnimationFrame(bloomUI.frame);bloomUI.frame=0;bloomUI.last=0;drawAIBloom(bloomUI.time,true);}
}
function tickAIBloom(now){
  if(!bloomUI.visible||document.hidden||document.body.dataset.screen!=="app"||castReduceMotion()){bloomUI.frame=0;syncAIBloom();return;}
  const dt=bloomUI.last?Math.min(.045,(now-bloomUI.last)/1000):0;bloomUI.last=now;
  bloomUI.energy+=((bloomUI.hover?1:0)-bloomUI.energy)*.075;
  const pulse=Math.max(0,1-(bloomUI.time-bloomUI.pulseAt)/1.7);
  bloomUI.time+=dt*(1+bloomUI.energy*.3+pulse*.55);
  drawAIBloom(bloomUI.time,false);bloomUI.frame=requestAnimationFrame(tickAIBloom);
}
function bloomFlight(index,progress){
  const spread=[-96,-9,91,49,-61,9,101,-51,48,-100,20][index],height=[136,158,133,150,110,125,105,155,100,119,143][index];
  const lift=Math.sin(Math.PI*progress),wobble=Math.sin(progress*Math.PI*2+index)*5*lift;
  return {x:120+spread*Math.sin(progress*Math.PI/2)+wobble,y:188-lift*height-progress*8,
    scale:.42+.58*Math.pow(lift,.65),turn:Math.sin(progress*Math.PI*2+index)*9+spread*.09,
    opacity:Math.min(1,progress/.12,(1-progress)/.18)};
}
function bloomStar(ctx,x,y,radius,color,alpha,turn=0){
  ctx.save();ctx.translate(x,y);ctx.rotate(turn);ctx.globalAlpha=alpha;ctx.fillStyle=color;ctx.beginPath();
  ctx.moveTo(0,-radius);ctx.quadraticCurveTo(radius*.2,-radius*.2,radius,0);ctx.quadraticCurveTo(radius*.2,radius*.2,0,radius);
  ctx.quadraticCurveTo(-radius*.2,radius*.2,-radius,0);ctx.quadraticCurveTo(-radius*.2,-radius*.2,0,-radius);ctx.fill();ctx.restore();
}
function drawAIBloom(time,still){
  const ctx=bloomUI.context,pulse=still?0:Math.max(0,1-(time-bloomUI.pulseAt)/1.7),energy=bloomUI.energy;
  const colors=castUI.scene==="bank"?["#c995c8","#ddb16d","#87acd7","#e59b94"]:castUI.scene==="leaderboard"?["#a18bff","#73b9ed","#e7bf78","#dc9fdb"]:["#7f90f3","#76c9ef","#dd9fda","#92d6c2"];
  if(ctx){
    ctx.setTransform(bloomUI.ratio,0,0,bloomUI.ratio,0,0);ctx.clearRect(0,0,240,210);
    const glow=ctx.createRadialGradient(120,133,8,120,133,90);glow.addColorStop(0,colors[0]+"46");glow.addColorStop(.48,colors[2]+"27");glow.addColorStop(1,colors[1]+"00");
    ctx.fillStyle=glow;ctx.fillRect(0,0,240,210);
    const core=ctx.createRadialGradient(120,186,1,120,180,38+pulse*16+energy*5);core.addColorStop(0,"#ffffffed");core.addColorStop(.2,colors[1]+"be");core.addColorStop(.52,colors[0]+"55");core.addColorStop(1,colors[2]+"00");
    ctx.fillStyle=core;ctx.fillRect(50,110,140,100);
    // Colored silk-like streams curl upwards from the same crystal source.
    const ribbon=ctx.createLinearGradient(58,182,174,28);ribbon.addColorStop(0,colors[1]);ribbon.addColorStop(.45,colors[0]);ribbon.addColorStop(1,colors[2]);
    ctx.save();ctx.strokeStyle=ribbon;ctx.lineCap="round";ctx.globalAlpha=.37+pulse*.18;ctx.lineWidth=3.2;
    ctx.beginPath();ctx.moveTo(117,185);ctx.bezierCurveTo(64,154,184,122,134,65);ctx.bezierCurveTo(104,34,55,52,66,82);ctx.stroke();
    ctx.globalAlpha=.24+pulse*.12;ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(123,186);ctx.bezierCurveTo(181,155,70,100,109,54);ctx.bezierCurveTo(135,24,181,48,167,77);ctx.stroke();ctx.restore();
    // Glassy orbital ribbons turn around a shared source.
    for(let i=0;i<3;i++){
      ctx.save();ctx.translate(120,187);ctx.rotate(-.22+i*.23);ctx.scale(1,.31+i*.04);
      const radius=28+i*10+pulse*8,arc=time*(i%2?-1:1)*.7+i;
      ctx.lineWidth=1.5;ctx.strokeStyle=colors[i];ctx.globalAlpha=.35;ctx.beginPath();ctx.arc(0,0,radius,0,Math.PI*2);ctx.stroke();
      ctx.globalAlpha=.85;ctx.lineWidth=2.5;ctx.beginPath();ctx.arc(0,0,radius,arc,arc+1.4);ctx.stroke();ctx.restore();
    }
    if(!still){
      // Fine rising dust, with brighter starbursts after touching the core.
      for(let i=0;i<32;i++){
        const p=(time*(.18+(i%4)*.025)+i*.173)%1,fan=Math.sin(i*2.7),height=80+(i%7)*12;
        const x=120+fan*Math.sin(p*Math.PI/2)*(22+(i%5)*18)*(1+pulse*.3),y=184-p*height;
        const alpha=Math.sin(p*Math.PI)*(.4+pulse*.35),radius=.6+(i%3)*.6;
        if(i%5===0)bloomStar(ctx,x,y,2.4+pulse*1.8,colors[i%4],alpha,time*.4);
        else{ctx.globalAlpha=alpha;ctx.fillStyle=colors[i%4];ctx.beginPath();ctx.arc(x,y,radius,0,Math.PI*2);ctx.fill();}
      }
      const burst=(time*.38)%1;
      ctx.strokeStyle=colors[1];ctx.globalAlpha=(1-burst)*.3;ctx.lineWidth=1;ctx.beginPath();ctx.ellipse(120,187,25+burst*35,7+burst*10,0,0,Math.PI*2);ctx.stroke();
      if(pulse>0){
        const age=1-pulse,travel=1-Math.pow(1-age,2.4);
        for(let i=0;i<18;i++){
          const angle=-Math.PI+.17+i/(18-1)*(Math.PI-.34),reach=55+(i%5)*12;
          const x=120+Math.cos(angle)*reach*travel,y=185+Math.sin(angle)*reach*travel+age*age*19;
          bloomStar(ctx,x,y,1.2+pulse*2.6,colors[i%4],pulse*.9,age*2+i);
        }
      }
    }
  }
  for(const [index,token] of bloomUI.tokens.entries()){
    const duration=5.5+(index%4)*.36,progress=still ? .5 : ((time+index*.43+1.15)%duration)/duration;
    let point=bloomFlight(index,progress);
    if(still){
      const x=[29,110,211,166,65,128,217,64,166,25,137][index],y=[81,28,81,48,120,96,120,47,126,111,64][index];
      point={x,y,scale:.87,turn:(index%3-1)*9,opacity:1};
    }
    if(ctx&&!still){
      // Each logo leaves its own colored curved wake, rather than a spoke.
      ctx.lineCap="round";ctx.strokeStyle=CAST_MEMBERS[index].color;
      for(let segment=1;segment<=7;segment++){
        const previous=progress-segment*.014,next=progress-(segment-1)*.014;if(previous<0)break;
        const a=bloomFlight(index,previous),b=bloomFlight(index,next);
        ctx.globalAlpha=point.opacity*(1-segment/8)*(.42+pulse*.2);ctx.lineWidth=1.1+(1-segment/8)*2.5;
        ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke();
      }
    }
    token.style.transform=`translate3d(${(point.x-16).toFixed(2)}px,${(point.y-16).toFixed(2)}px,0) rotate(${point.turn.toFixed(2)}deg) scale(${point.scale.toFixed(3)})`;
    token.style.opacity=point.opacity.toFixed(3);
  }
  if(ctx){
    bloomStar(ctx,34,46,3.2,colors[1],.65,still?0:time*.2);bloomStar(ctx,204,33,4.1,colors[2],.6,still?0:-time*.15);
    bloomStar(ctx,191,153,2.8,colors[0],.5,still?0:time*.3);ctx.globalAlpha=1;
  }
}
