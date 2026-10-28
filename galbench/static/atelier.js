"use strict";

// Decorative preferences never change the selected model or its configuration.
const atelierUI={selected:"gpt",frame:0};
function wallCoverOrder(value){let hash=2166136261;for(const ch of value)hash=Math.imul(hash^ch.charCodeAt(0),16777619);return hash>>>0;}

function initAtelierUI(){
  const dock=$("title-partners");
  for(const member of CAST_MEMBERS){
    const button=node("button","title-partner"),icon=node("img");button.type="button";button.dataset.member=member.id;
    button.title=member.name;button.setAttribute("aria-label",`预览 ${member.name} 形象`);button.setAttribute("aria-pressed","false");
    button.style.setProperty("--partner-color",member.color);icon.src=castIcon(member);icon.alt="";icon.width=22;icon.height=22;button.append(icon);
    button.addEventListener("click",()=>paintTitlePartners(member.id));dock.append(button);
  }
  dock.addEventListener("keydown",event=>{
    if(!["ArrowLeft","ArrowRight","Home","End"].includes(event.key))return;
    const buttons=[...dock.children],index=buttons.indexOf(document.activeElement);if(index<0)return;event.preventDefault();
    const next=event.key==="Home"?0:event.key==="End"?buttons.length-1:(index+(event.key==="ArrowRight"?1:-1)+buttons.length)%buttons.length;
    buttons[next].focus({preventScroll:true});buttons[next].click();
  });
  paintTitlePartners(atelierUI.selected);
  const desk=$("title-polaroids");
  const reset=()=>{cancelAnimationFrame(atelierUI.frame);atelierUI.frame=0;desk.style.removeProperty("--desk-x");desk.style.removeProperty("--desk-y");};
  desk.addEventListener("pointermove",event=>{
    if(reducedMotion()||!matchMedia("(hover:hover) and (pointer:fine)").matches||atelierUI.frame)return;
    const x=event.clientX,y=event.clientY;
    atelierUI.frame=requestAnimationFrame(()=>{atelierUI.frame=0;const r=desk.getBoundingClientRect();desk.style.setProperty("--desk-x",`${(x-r.left-r.width/2)*.025}px`);desk.style.setProperty("--desk-y",`${(y-r.top-r.height/2)*.035}px`);});
  });
  desk.addEventListener("pointerleave",reset);document.addEventListener("visibilitychange",()=>{if(document.hidden)reset();});
  matchMedia("(prefers-reduced-motion: reduce)").addEventListener("change",reset);
  $("cover-search").addEventListener("input",renderCoverCredits);$("cover-source").addEventListener("change",renderCoverCredits);
}

function paintTitlePartners(id){
  const index=CAST_MEMBERS.findIndex(m=>m.id===id);if(index<0)return;
  const restore=document.activeElement?.classList.contains("title-polaroid");atelierUI.selected=id;
  const desk=$("title-polaroids");desk.replaceChildren();
  for(const offset of [-1,0,1]){
    const member=CAST_MEMBERS[(index+offset+CAST_MEMBERS.length)%CAST_MEMBERS.length],card=node("button",`title-polaroid position-${offset+1}`),picture=node("span","title-polaroid-picture"),art=node("img");
    card.type="button";card.dataset.member=member.id;card.style.setProperty("--partner-color",member.color);card.setAttribute("aria-label",`预览 ${member.name} 收藏卡`);card.setAttribute("aria-pressed",String(offset===0));
    art.src=`/ai-cast/card-${member.id}.jpg`;art.alt=`${member.name} 的社区形象`;art.decoding="async";art.draggable=false;picture.append(art);
    const footer=node("span","title-polaroid-footer"),logo=node("img");logo.src=castIcon(member);logo.alt="";logo.width=19;logo.height=19;footer.append(logo,node("strong","",member.name),node("span","","✦"));
    card.append(picture,footer);card.addEventListener("click",()=>paintTitlePartners(member.id));desk.append(card);
  }
  for(const button of $("title-partners").children)button.setAttribute("aria-pressed",String(button.dataset.member===id));
  $("title-screen").style.setProperty("--title-partner-color",CAST_MEMBERS[index].color);
  if(restore)desk.querySelector(".position-1").focus({preventScroll:true});
}

function coverSourceLink(cover){
  try{
    const url=new URL(cover.source_url);
    if(url.protocol!=="https:"||url.username||url.password)return null;
    if(url.hostname==="store.steampowered.com"&&/^\/app\/\d+(?:\/|$)/.test(url.pathname))return url.href;
    if(url.hostname==="vndb.org"&&/^\/v\d+$/.test(url.pathname))return url.href;
    if(url.hostname==="github.com"&&url.pathname.startsWith("/renpy/renpy/"))return url.href;
  }catch(_){}
  return null;
}

function renderCoverCredits(){
  const query=normalizedTitle($("cover-search").value),source=$("cover-source").value;
  const covers=novelUI.covers.filter(c=>(source==="all"||c.source===source)&&(!query||[c.title,...(c.aliases||[])].some(t=>normalizedTitle(t).includes(query))));
  $("cover-count").textContent=`${covers.length} / ${novelUI.covers.length}`;
  $("cover-credit-list").replaceChildren(...covers.map(c=>{
    const url=coverSourceLink(c),card=node(url?"a":"div","cover-credit"),image=node("img");image.src=c.image;image.alt="";image.loading="lazy";image.decoding="async";
    if(url){card.href=url;card.target="_blank";card.rel="noopener noreferrer";}
    card.append(image,node("strong","",c.title),node("span","",(c.source||"来源")+(url?" ↗":"")));return card;
  }));
  if(!covers.length)$("cover-credit-list").append(node("p","cover-empty","换个名字，再找找。"));
}

function setQuestionScene(title,board){
  const dialog=$("question-dialog"),heading=dialog.querySelector(".modal-heading"),cover=coverFor(title);
  dialog.dataset.board=["text","vision","story"].includes(board)?board:"text";
  heading.querySelector(".question-cover-jacket")?.remove();heading.classList.toggle("with-cover",!!cover);
  if(cover){const jacket=node("span","question-cover-jacket"),image=node("img");image.src=cover.image;image.alt="";image.decoding="async";jacket.append(image);heading.append(jacket);}
}
