"use strict";

const novelUI={covers:[],coverPromise:null,wallPaused:false,entering:false,bankWork:"",shelfSignature:"",previewKey:null,
  previewAt:0,previewJob:null,previewSeq:0,previewCache:new Map(),revealObserver:null,revealed:new WeakSet(),observed:new Set(),lastResult:null};
const reducedMotion=()=>matchMedia("(prefers-reduced-motion: reduce)").matches;
const normalizedTitle=value=>String(value||"").toLowerCase().replace(/[^\p{L}\p{N}]/gu,"");
function coverFor(title){const key=normalizedTitle(title);return novelUI.covers.find(c=>[c.title,...(c.aliases||[])].some(t=>normalizedTitle(t)===key));}

function initNovelUI(){
  initVoxelUI();
  initAtelierUI();
  const comparison=node("details","rank-comparison-note"),comparisonHeading=node("summary","","排名规则"),comparisonGrid=node("div","rank-comparison-grid");
  for(const [name,text] of [["300 分","同一场三榜各 100，未齐不按零分计"],["Agent","显示实际回传 ID 或声明来源，客户端不是模型"]]){const cell=node("div");cell.append(node("strong","",name),node("span","",text));comparisonGrid.append(cell);}
  comparison.append(comparisonHeading,comparisonGrid,node("p","","总分取同一场三榜成绩；单榜取最佳完整自动成绩。复评不重复计场，按客户端与模型配置分别记录。模型未知或回传多个模型时不排名；手动名称标注为未核实。"));
  $("standings-content").before(comparison);
  $("enter-arena").addEventListener("click",()=>enterArena());
  $("show-title").addEventListener("click",event=>{event.preventDefault();showTitleScreen();});
  $("toggle-cover-motion").addEventListener("click",()=>{novelUI.wallPaused=!novelUI.wallPaused;syncCoverMotion();});
  for(const button of document.querySelectorAll("[data-cover-credits]"))button.addEventListener("click",()=>$("cover-credits").showModal());
  $("close-cover-credits").addEventListener("click",()=>$("cover-credits").close());
  $("cover-credits").addEventListener("click",event=>{if(event.target===$("cover-credits")){const r=event.target.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)event.target.close();}});
  $("bank-all-works").addEventListener("click",()=>{novelUI.bankWork="";bankPage=1;loadBank();});
  document.addEventListener("visibilitychange",syncCoverMotion);
  matchMedia("(prefers-reduced-motion: reduce)").addEventListener("change",syncCoverMotion);
  try{if(sessionStorage.getItem("galbench.entered.v2")==="1")enterArena(true);}catch(_){}
  loadCovers();syncCoverMotion();
}
function showTitleScreen(){
  novelUI.entering=false;$("title-screen").hidden=false;$("app-shell").hidden=true;$("app-shell").inert=true;
  document.body.dataset.screen="title";$("title-screen").classList.remove("is-leaving");$("enter-arena").disabled=false;
  $("enter-arena").replaceChildren(document.createTextNode(busy?"返回进行中的比赛 ":"进入攻略大厅 "),node("span","","↗"));
  try{sessionStorage.removeItem("galbench.entered.v2");}catch(_){}
  window.scrollTo({top:0,behavior:"instant"});$("enter-arena").focus({preventScroll:true});syncCoverMotion();
}
function enterArena(immediate=false){
  if(novelUI.entering||document.body.dataset.screen==="app")return;novelUI.entering=true;$("enter-arena").disabled=true;
  $("title-screen").classList.add("is-leaving");
  const finish=()=>{
    $("title-screen").hidden=true;$("app-shell").hidden=false;$("app-shell").inert=false;document.body.dataset.screen="app";
    novelUI.entering=false;try{sessionStorage.setItem("galbench.entered.v2","1");}catch(_){}
    window.scrollTo({top:0,behavior:"instant"});$("show-title").focus({preventScroll:true});syncCoverMotion();observeNovelContent();
    if(!immediate)revealView($("dashboard-view"));
  };
  if(immediate||reducedMotion())finish();else setTimeout(finish,280);
}
function syncCoverMotion(){
  const paused=novelUI.wallPaused||document.hidden||document.body.dataset.screen!=="title"||reducedMotion();
  $("cover-wall").dataset.paused=String(paused);$("toggle-cover-motion").setAttribute("aria-pressed",String(novelUI.wallPaused||reducedMotion()));
  $("toggle-cover-motion").textContent=reducedMotion()?"◇ 静态展示":novelUI.wallPaused?"▷ 继续滚动":"Ⅱ 暂停滚动";$("toggle-cover-motion").disabled=reducedMotion();
}
async function loadCovers(){
  if(novelUI.coverPromise)return novelUI.coverPromise;
  novelUI.coverPromise=(async()=>{
    try{
      const data=await api("/api/covers");novelUI.covers=(data.covers||[]).filter(c=>typeof c.title==="string"&&/^\/covers\/[a-z0-9-]+\.jpg$/.test(c.image));
      $("cover-wall").replaceChildren();
      const wallCovers=[...novelUI.covers].sort((a,b)=>wallCoverOrder(a.id)-wallCoverOrder(b.id)).slice(0,48);
      for(let col=0;col<4;col++){
        const lane=node("div","cover-lane"),track=node("div","cover-track");track.style.setProperty("--scroll-seconds",`${150+col*18}s`);
        const covers=wallCovers.filter((_,index)=>index%4===col);
        for(let repeat=0;repeat<2;repeat++){
          const group=node("div","cover-track-group");
          for(const c of covers){const frame=node("figure","wall-cover"),image=node("img");image.src=c.image;image.alt="";image.width=600;image.height=900;image.loading="lazy";image.decoding="async";frame.append(image);group.append(frame);}
          track.append(group);
        }lane.append(track);$("cover-wall").append(lane);
      }
      $("cover-wall-count").textContent=`${novelUI.covers.length} 张故事封面`;
      renderCoverCredits();
      novelUI.shelfSignature="";if(novelUI.bankWorks)renderBankShelf(novelUI.bankWorks,novelUI.bankTotal);
    }catch(_){$("cover-wall-count").textContent="封面暂不可用";$("cover-wall").classList.add("covers-unavailable");$("cover-credit-list").append(node("p","notice","封面目录暂时不可用，评测功能仍可正常使用。"));}
  })();return novelUI.coverPromise;
}

function observeNovelContent(){
  if(!novelUI.revealObserver&&"IntersectionObserver" in window){
    novelUI.revealObserver=new IntersectionObserver(entries=>{
      for(const entry of entries)if(entry.isIntersecting){novelUI.revealObserver.unobserve(entry.target);novelUI.observed.delete(entry.target);if(novelUI.revealed.has(entry.target))continue;novelUI.revealed.add(entry.target);
        if(!reducedMotion())entry.target.animate([{opacity:0,transform:"translateY(17px)"},{opacity:1,transform:"translateY(0)"}],{duration:480,easing:"cubic-bezier(.2,.7,.25,1)"});}
    },{threshold:0,rootMargin:"0px 0px 20px 0px"});
  }
  for(const el of novelUI.observed)if(!el.isConnected){novelUI.revealObserver?.unobserve(el);novelUI.observed.delete(el);}
  for(const el of document.querySelectorAll("#app-shell .panel,.question-card,.record-card,.podium-person"))if(!novelUI.revealed.has(el)&&!novelUI.observed.has(el)){novelUI.revealObserver?.observe(el);novelUI.observed.add(el);}
}
function revealView(el){
  if(!el||reducedMotion()||document.body.dataset.screen!=="app")return;
  el.getAnimations().forEach(animation=>animation.cancel());
  el.animate([{opacity:.25,transform:"translateY(12px)"},{opacity:1,transform:"translateY(0)"}],{duration:350,easing:"cubic-bezier(.2,.7,.25,1)"});observeNovelContent();
}

async function renderQuestionMemory(job){
  if(novelUI.previewJob!==job.id){novelUI.previewJob=job.id;novelUI.previewKey=null;novelUI.previewAt=0;novelUI.previewSeq++;
    $("quest-memory-work").textContent="正在翻开第一张题卡";$("quest-memory-excerpt").textContent="";$("quest-memory-id").textContent="";$("quest-memory-image").replaceChildren(node("span","","✧"));}
  const phase=questPhase(job);$("quest-memory-label").textContent=phase==="grading"?"裁判核对中":["finished","review","stopped"].includes(phase)?"最近题卡":"题卡翻阅中";
  const active=(phase==="grading"?job.active_grades:job.active_calls)||[];
  const calls=active.filter(c=>c.board===questUI.board),candidates=calls.length?calls:active.length?active:job.recent_calls||[];
  if(!candidates.length)return;
  const current=candidates.find(c=>c.question_id===novelUI.previewKey);
  if(novelUI.previewKey&&Date.now()-novelUI.previewAt<6500)return;
  const call=candidates.find(c=>c.question_id!==novelUI.previewKey)||current||candidates[0];if(!call||call.question_id===novelUI.previewKey)return;
  novelUI.previewKey=call.question_id;novelUI.previewAt=Date.now();const sequence=++novelUI.previewSeq,profile=job.dataset_profile||bootstrap.default_dataset,key=profile+":"+call.question_id;
  try{
    let preview=novelUI.previewCache.get(key);if(!preview){preview=await api("/api/preview?"+new URLSearchParams({profile,id:call.question_id}));novelUI.previewCache.set(key,preview);}
    if(sequence!==novelUI.previewSeq||novelUI.previewJob!==job.id)return;
    $("quest-memory-work").textContent=preview.work_title;$("quest-memory-excerpt").textContent=preview.excerpt||"翻开题卡，寻找故事里的线索。";$("quest-memory-id").textContent=`${preview.id} · ${labels[preview.board]}`;
    const art=$("quest-memory-image");art.replaceChildren();art.classList.toggle("has-image",!!preview.image_url);
    if(preview.image_url){const image=node("img");image.src=preview.image_url;image.alt="";image.decoding="async";art.append(image);}else art.append(node("span","",preview.board==="story"?"叙":"文"));
    if(!reducedMotion())$("quest-memory").animate([{opacity:.1,transform:"translateY(8px) rotate(-2deg)"},{opacity:1,transform:"translateY(0) rotate(-2deg)"}],{duration:400,easing:"ease-out"});
  }catch(_){if(sequence===novelUI.previewSeq){$("quest-memory-work").textContent=call.work_title||"正在阅读题目";$("quest-memory-excerpt").textContent="片段预览暂时不可用，答题仍在继续。";}}
}

function buildPodium(group,division,maximum=100){
  const finalists=group.filter(r=>r.eligible&&r.rank&&r.rank<=5).slice(0,5),stage=node("div","podium-stage");stage.setAttribute("role","group");stage.setAttribute("aria-label",`${divisionNames[division]||division} 前五名擂台`);
  stage.append(node("span","podium-orbit orbit-one"),node("span","podium-orbit orbit-two"),node("span","podium-star podium-star-a","✧"),node("span","podium-star podium-star-b","✦"));
  const floor=node("div","podium-floor"),slots=node("div","podium-slots");stage.append(floor,slots);
  for(let index=0;index<5;index++){
    const row=finalists[index],rank=row?.rank||index+1,tied=row&&finalists.filter(r=>r.rank===rank).length>1;
    const button=node("button",`podium-person podium-slot-${index+1}${row?"":" is-empty"}`);button.type="button";button.disabled=!row;button.dataset.podiumRank=String(rank);button.style.setProperty("--tier",String(Math.min(rank,5)));button.style.setProperty("--podium-order",String(index));
    button.title=row?`${row.model} · 第 ${rank} 名 · ${scoreValue(row.score)} / ${maximum} · 点击查看历史`:"这个名次等待完整自动成绩";
    const caption=node("span","podium-caption",(tied?"并列":"")+({1:"冠军",2:"亚军",3:"季军",4:"第四名",5:"第五名"}[rank]||`第 ${rank} 名`));
    const avatar=node("span","podium-avatar");avatar.append(node("i","podium-crown",rank===1?"♛":"✦"),node("span","podium-robot-head"),node("span","podium-robot-body"));
    if(row)avatar.dataset.vendor=row.vendor;
    const info=node("span","podium-info");info.append(node("strong","podium-model",row?.model||"等你登台"),node("span","podium-points",row?scoreValue(row.score):"—"),node("small","",row?`/ ${maximum}`:"等待有效成绩"));
    const plinth=node("span","podium-plinth");plinth.append(node("b","",String(rank).padStart(2,"0")));
    button.append(caption,avatar,info,plinth);if(row)button.addEventListener("click",()=>selectModel(row.model_key));slots.append(button);
  }
  registerVoxelStage(stage,Array.from({length:5},(_,index)=>finalists[index]||null));
  return stage;
}

function resultCounts(value){const c=value?.counts||{};return {correct:c.C||0,partial:c.P||0,wrong:c.W||0,abstain:c.A||0,invalid:c.V||0,pending:(c.ERROR||0)+(c.HOLD||0)};}
function renderResultOverview(result){
  const summary=result.summary,total=result.scores.length,scored=result.scores.filter(r=>r.item_score!=null).length,correct=result.scores.filter(r=>r.status==="C").length,wrong=result.scores.filter(r=>r.status==="W").length;
  const overview=$("result-overview");overview.replaceChildren();overview.dataset.state=summary.stopped?"stopped":summary.status;
  const mascot=node("div","result-mascot");mascot.setAttribute("aria-hidden","true");mascot.append(node("span","mascot-spark","✦"),node("span","mascot-face"),node("span","mascot-letter","♡"));
  const copy=node("div","result-overview-copy");copy.append(node("h3","",summary.stopped?"本轮已停止，答卷已保留。":scored<total?"成绩已生成，部分题目待核验。":"攻略完成，成绩揭晓。"));
  const complete=["text","vision","story"].every(b=>Number.isFinite(summary.boards[b]?.gal_score));
  const totalmark=node("span","result-total");totalmark.append(node("small","",summary.stopped?"保留总分":Object.values(summary.boards).some(b=>b.manual_scored_samples)?"总分 · 含人工分":"本场总分"),node("strong","",complete?scoreValue(["text","vision","story"].reduce((sum,b)=>sum+summary.boards[b].gal_score,0)):"三榜未齐"),node("small","",complete?"/ 300":"补齐三榜后计算"));copy.append(totalmark);
  const stats=node("div","result-quick-stats");for(const [number,label,cls] of [[`${scored}/${total}`,"已评分样本","coverage"],[correct,"完全正确","correct"],[wrong,"明确答错","wrong"],[total-scored,"等待核验","pending"]]){const item=node("span",`quick-${cls}`);item.append(node("strong","",String(number)),node("small","",label));stats.append(item);}
  overview.append(mascot,copy,stats);novelUI.lastResult=summary.run_id;
}
function makeRouteResult(board,value,summary,legacy){
  const card=node("div",`score-card route-result result-${board}${!value?" result-not-played":""}`),count=resultCounts(value),scored=count.correct+count.partial+count.wrong+count.abstain+count.invalid;
  const top=node("div","result-card-top"),mark=node("span","result-route-mark",{text:"文",vision:"像",story:"叙"}[board]);
  const heading=node("div");heading.append(node("div","score-label",board==="story"&&legacy?"剧情理解（旧版）":labels[board]));
  top.append(mark,heading,node("span","result-card-seal",!value?"未出场":summary.stopped?"保留记录":value.gal_score==null?"待补完":value.manual_scored_samples?"含人工分":"已结算"));card.append(top);
  const display=value?value.gal_score??value.display_score:null,ring=node("div","result-score-ring"),score=node("div","score-value",value?scoreValue(display):"未测试");
  if(display==null)score.classList.add("score-state");score.append(node("small","",value?" / 100":""));ring.style.setProperty("--score-angle",`${display==null?0:Math.max(0,Math.min(100,display))*3.6}deg`);ring.classList.toggle("is-provisional",!!value&&value.gal_score==null);ring.append(score,node("span","result-score-caption",!value?"下次再相遇":value.gal_score!=null?"本线得分":value.display_score_kind==="known_only"?"已评分参考分":"已确认得分下界"));
  const rates=node("div","result-rates");for(const [label,number,cls] of [["全对率",count.correct,"correct"],["部分正确率",count.partial,"partial"],["答错率",count.wrong,"wrong"]]){const item=node("div",`rate-${cls}`);item.append(node("span","",label),node("strong","",scored?`${(100*number/scored).toFixed(1)}%`:"—"),node("small","",scored?`${number} / ${scored} 个已评分样本`:"尚无有效评分"));rates.append(item);}
  const centerpiece=node("div","result-card-center");centerpiece.append(ring,rates);card.append(centerpiece);
  const hearts=node("div","result-hearts");hearts.setAttribute("aria-hidden","true");for(let i=0;i<5;i++)hearts.append(node("span",display!=null&&display>=(i+1)*20?"is-lit":"","♥"));
  const mood=!value?"这条路线，下次再来相遇。":value.gal_score==null?"先保留线索，等待完整核验。":value.gal_score>=90?"这条攻略线，游刃有余。":value.gal_score>=75?"熟悉故事，偶有错过。":value.gal_score>=50?"记得一些线索，支线仍待补完。":"这条路线，还有许多故事可读。";
  const moodline=node("div","result-mood");moodline.append(hearts,node("span","",mood));card.append(moodline);
  const bar=node("div","answer-distribution");bar.setAttribute("role","img");bar.setAttribute("aria-label",`全对 ${count.correct}，部分正确 ${count.partial}，答错 ${count.wrong}，弃答 ${count.abstain}，无效 ${count.invalid}，未评分 ${count.pending}`);
  const legend=node("div","answer-legend"),parts=[["correct","全对",count.correct],["partial","部分正确",count.partial],["wrong","答错",count.wrong],["abstain","弃答",count.abstain],["invalid","无效",count.invalid],["pending","未评分",count.pending]];
  for(const [key,label,amount] of parts){if(amount){const piece=node("span",`distribution-${key}`);piece.style.flex=String(amount);piece.title=`${label} ${amount} 个样本`;bar.append(piece);}if(amount||["correct","partial","wrong"].includes(key)){const item=node("span",`legend-${key}`);item.append(node("i"),node("span","",label),node("b","",String(amount)));legend.append(item);}}
  card.append(bar,legend);
  const details=node("div","result-card-details");details.append(node("div","score-sub",value?`${value.questions} 道题 · ${value.samples} 个样本 · 已评分 ${value.scored_samples??scored}/${value.samples}`:"本次未选择这条路线"));
  if(value?.gal_score==null&&value){details.append(node("div","score-sub provisional-label",`完整得分范围 ${scoreValue(value.score_lower_bound)}–${scoreValue(value.score_upper_bound)} / 100`));if(!scored)details.append(node("div","score-sub","暂无有效评分，不能据此评价模型能力。"));}
  if(value?.answer_timing?.samples)details.append(node("div","score-sub",`平均回答 ${duration(value.answer_timing.mean_seconds)}`));
  if(value?.manual_scored_samples)details.append(node("div","score-sub",`含 ${value.manual_scored_samples} 个人工评分样本`));
  if(board==="story"&&value)details.append(node("div","score-sub","总结维度：事件 / 关系 / 忠实 / 表达"));card.append(details);return card;
}

function renderBankShelf(works,total){
  novelUI.bankWorks=works||[];novelUI.bankTotal=total||0;$("bank-collection-total").textContent=`${works?.length||0} 部作品 · ${total||0} 道题`;
  const signature=JSON.stringify([works,novelUI.bankWork,novelUI.covers.length]);if(signature===novelUI.shelfSignature)return;novelUI.shelfSignature=signature;
  const shelf=$("bank-shelf"),scroll=shelf.scrollLeft,focused=document.activeElement?.closest(".shelf-book")?.dataset.workTitle;
  shelf.replaceChildren(...(works||[]).map((work,index)=>{
    const button=node("button",`shelf-book${work.title===novelUI.bankWork?" is-selected":""}`);button.type="button";button.setAttribute("aria-pressed",String(work.title===novelUI.bankWork));button.title=`《${work.title}》 · ${work.count} 道题，点击筛选`;
    button.dataset.workTitle=work.title;
    const art=node("span","shelf-book-cover"),cover=coverFor(work.title);
    if(cover){const image=node("img");image.src=cover.image;image.alt="";image.loading="lazy";image.decoding="async";art.style.setProperty("--cover-image",`url("${cover.image}")`);art.classList.add("has-cover");art.append(image);if(cover.show_title_overlay)art.append(node("strong","cover-title-overlay",work.title));}else{art.classList.add("original-cover");art.style.setProperty("--book-hue",String(245+(index*21)%80));art.append(node("strong","",work.title),node("span","","✦"));}
    art.append(node("i","shelf-book-ribbon",work.title===novelUI.bankWork?"选中":""));button.append(art,node("strong","shelf-book-title",work.title),node("small","",`${work.count} 道题`));
    button.addEventListener("click",()=>{novelUI.bankWork=novelUI.bankWork===work.title?"":work.title;bankPage=1;loadBank();});return button;
  }));
  shelf.scrollLeft=scroll;if(focused)[...shelf.querySelectorAll(".shelf-book")].find(button=>button.dataset.workTitle===focused)?.focus({preventScroll:true});
}
