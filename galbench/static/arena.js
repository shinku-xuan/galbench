"use strict";
const arenaUI={data:null,board:"total",model:null,page:1,pageSize:12,lastFocus:null,toastTimer:null};
const divisionNames={agent_cli:"Agent 联赛"};
const harnessLabel=id=>(bootstrap?.agents||[]).find(a=>a.id===id)?.label||id;
const identityNames={reported:"CLI / 接口回传",declared:"手动声明 · 未核实",unknown:"实际模型未知",mixed:"回传多个模型 · 不计排名"};

function showToast(message) {
  const dialog=document.querySelector("dialog[open]");(dialog||document.body).append($("toast"));$("toast").classList.toggle("in-dialog",!!dialog);
  clearTimeout(arenaUI.toastTimer);$("toast").textContent=message;$("toast").hidden=false;
  arenaUI.toastTimer=setTimeout(()=>{$("toast").hidden=true;},4200);
}
function closeDialog(id) {if($(id).open)$(id).close();}
function openConfig(role="target") {
  if(!$("config-dialog").open)$("config-dialog").showModal();
  const id=role+"-cli-model";
  const el=$(id);if(el&&!el.disabled&&!el.closest("[hidden]"))el.focus();
}
function renderPlayerSlots() {
  if(!bootstrap)return;
  for(const role of ["target","judge"]) {
    const isDemo=mode==="mock_demo",agent=isAgent(role),disabled=role==="judge"&&!$("judge-enabled").checked,rulesOnly=role==="judge"&&!needsJudge();
    const model=isDemo?(role==="target"?"演示选手":"模拟裁判"):$(role+"-model-label").value.trim()||$(role+"-cli-model").value.trim()||"客户端默认 · 待确认";
    $(role+"-summary").textContent=rulesOnly?"固定答案 · 规则直判":disabled?"规则评分 / 人工复核":model;
    $(role+"-connection").textContent=rulesOnly?"本次不调用模型裁判":isDemo?"DEMO · 不消耗模型额度":disabled?"模型裁判已关闭":$(role+"-provider").selectedOptions[0]?.text.split(" · ")[0]+" · Harness";
  }
}
function renderHeroTitle(view) {
  const copy={dashboard:["和 AI 娘一起，","攻略故事的世界。"],leaderboard:["今夜的主角，","由答卷来决定。"],bank:["翻开这一页，","故事就有了回声。"],mistakes:["故事还有另一种","打开方式。"],issues:["小插曲，也能有","好结局。"]}[view];
  if(!copy)return;
  $("hero-title").replaceChildren(document.createTextNode(copy[0]),node("br"),node("em","",copy[1]));
  renderCastHero(view);
}
async function loadShowcase() {
  initCastHero();
  try {await loadCovers();}catch(_){/* The evaluation remains usable without cover decoration. */}
}
function celebrateFinish() {
  if(currentResult?.summary.status!=="complete"){showToast("本轮已结束，部分题目需要核验。");return;}
  showToast("挑战完成，成绩已存档 ✦");
  if(matchMedia("(prefers-reduced-motion: reduce)").matches||document.body.dataset.view!=="dashboard")return;
  $("celebration").replaceChildren();
  for(let i=0;i<24;i++){const bit=node("i");bit.style.setProperty("--x",(5+Math.random()*90)+"vw");bit.style.setProperty("--delay",Math.random()*.5+"s");bit.style.setProperty("--hue",["#8a6cf2","#fb91b4","#74cdb9","#ffd582"][i%4]);$("celebration").append(bit);}
  setTimeout(()=>$("celebration").replaceChildren(),2600);
}
function promptPreview(input,board) {
  if(!input)return "原始题目已归档，点击查看本次回答。";
  if(board==="story"&&input.excerpt)return input.excerpt.turns.slice(0,3).map(t=>`${t.speaker}：${t.text}`).join(" ");
  return input.prompt||input.stage_a?.prompt||"点开卡片，读取这一段故事。";
}
function makeQuestionCard(q,open) {
  const card=node("button",`question-card card-${q.board}${q.images?.length?" has-image":" no-image"}`);card.type="button";card.dataset.questionId=q.id;
  card.setAttribute("aria-label",`${q.id} · ${q.work_title||labels[q.board]} · 查看题目详情`);
  const visual=node("div","card-art");
  if(q.images?.length){const img=node("img");img.src=q.images[0];img.alt="本题角色立绘";img.loading="lazy";img.decoding="async";visual.append(img);}
  else {visual.append(node("span","card-glyph",q.board==="story"?"叙":"文"),node("p","card-excerpt",promptPreview(q.input,q.board)));}
  visual.append(node("span","card-route",labels[q.board]),node("span","card-open","↗"));
  const info=node("div","card-info"),meta=node("div","card-meta");
  meta.append(node("span","card-id",q.id));
  if(q.badge)meta.append(node("span",`status-tag tag-${q.status}`,q.manual?"人工 · "+q.badge:q.badge));
  else meta.append(node("span","route-badge",q.grading_route==="rules"?"规则评分":"语义评分"));
  info.append(meta,node("strong","card-title",q.work_title||"故事片段"));
  const line=node("div","card-bottom");
  if(q.repeat!==undefined)line.append(node("span","",`第 ${q.repeat+1} 轮`));
  if(q.score!=null||q.badge)line.append(node("strong","",q.score==null?"待补分":(100*q.score).toFixed(1)+" / 100"));
  if(line.childNodes.length)info.append(line);
  card.append(visual,info);card.addEventListener("click",open);return card;
}
function showQuestionDialog(title,kicker,body) {
  arenaUI.lastFocus=document.activeElement;$("question-dialog-title").textContent=title;$("question-dialog-kicker").textContent=kicker;
  const content=$("question-dialog-content"),images=[...body.querySelectorAll(".question-portrait")];content.replaceChildren();content.classList.toggle("has-gallery",!!images.length);
  if(images.length){const gallery=node("aside","detail-gallery");for(const img of images){img.loading="eager";gallery.append(img);}content.append(gallery);}
  content.append(body);if(!$("question-dialog").open)$("question-dialog").showModal();content.scrollTop=0;
}
function openResultQuestion(row) {showQuestionDialog(row.work_title||row.work,`${labels[row.board]} · ${row.question_id} · 第 ${row.repeat+1} 轮`,resultQuestionBody(row));}
function openBankQuestion(q) {
  const body=node("div","question-body");body.append(node("h4","","本次挑战"),node("p","question-instruction",q.input.prompt||q.input.stage_a?.prompt));
  if(q.input.scope)body.append(node("p","muted small",Object.values(q.input.scope).filter(v=>typeof v==="string").join(" · ")));
  if(q.input.excerpt)body.append(node("pre","",q.input.excerpt.turns.map(t=>`${t.speaker}：${t.text}`).join("\n")));
  const range=q.input.summary_length||q.input.stage_a?.summary_length;if(range)body.append(node("p","notice",`总结要求 ${range.min}–${range.max} 字`));
  for(const source of q.images||[]){const img=node("img","question-portrait");img.src=source;img.alt="本题角色立绘";body.append(img);}
  body.append(node("p","muted small",q.grading_route==="rules"?"核验名称与别名，规则直接评分。":"按题目事实要点与总结维度进行语义评分。"));
  showQuestionDialog(q.work_title,`${labels[q.board]} · ${q.id}`,body);
}
async function loadArena() {
  try {
    arenaUI.data=await api("/api/arena");const data=arenaUI.data;
    const previous=$("rank-profile").value;$("rank-profile").replaceChildren();
    for(const p of data.profiles){const option=node("option","",p.label);option.value=p.id;$("rank-profile").append(option);}
    $("rank-profile").value=data.profiles.some(p=>p.id===previous)?previous:bootstrap.default_dataset;
    if(!$("rank-profile").value&&data.profiles.length)$("rank-profile").value=data.profiles[0].id;
    const oldVendor=$("history-vendor").value;$("history-vendor").replaceChildren(new Option("全部厂商","all"));
    for(const vendor of [...new Set(data.records.map(r=>r.vendor))].sort())$("history-vendor").append(new Option(vendor,vendor));
    if([...$("history-vendor").options].some(o=>o.value===oldVendor))$("history-vendor").value=oldVendor;
    $("season-label").textContent=`第 ${data.season} 赛季`;
    $("restore-rankings").hidden=!data.can_restore;
    const real=data.records.filter(r=>r.mode!=="mock_demo"),matches=new Set(real.map(r=>r.root_run_id)).size,players=new Set(real.map(r=>r.model_key)).size;
    $("arena-caption").textContent=`${players} 位真实选手 · ${matches} 场实测 · ${data.records.length} 份战绩${data.skipped_archives?` · ${data.skipped_archives} 份损坏记录已略过`:""}`;
    renderStandings();renderRoster();renderArchive();
  }catch(exc){$("standings-content").replaceChildren(node("p","notice error",exc.message));$("history-list").replaceChildren(node("p","muted small","战绩暂时无法读取，请刷新重试。"));}
}
function modelAvatar(model,vendor,className="") {
  const avatar=node("span","model-avatar "+className,(vendor==="OpenAI"?"O":vendor==="DeepSeek"?"D":model.slice(0,1)).toUpperCase());avatar.dataset.vendor=vendor;return avatar;
}
function selectModel(key) {
  arenaUI.model=key;arenaUI.page=1;$("history-search").value="";$("history-vendor").value="all";$("history-state").value="all";$("history-range").value="all";
  renderRoster();renderArchive();$("history-panel").scrollIntoView({behavior:matchMedia("(prefers-reduced-motion: reduce)").matches?"auto":"smooth",block:"start"});
}
function renderStandings() {
  const data=arenaUI.data;if(!data)return;
  const entries=data.entries.filter(r=>r.board===arenaUI.board&&r.dataset_key===$("rank-profile").value&&($("rank-mode").value==="all"||r.transport===$("rank-mode").value));
  $("standings-content").replaceChildren();
  const maximum=arenaUI.board==="total"?300:100;
  for(const [value,label] of [["high",maximum===300?"270–300 分":"90–100 分"],["middle",maximum===300?"180–269 分":"60–89 分"],["low",maximum===300?"低于 180 分":"低于 60 分"]])$("history-range").querySelector(`[value="${value}"]`).textContent=label;
  const divisions=[...new Set(entries.map(r=>r.mode))].sort((a,b)=>["agent_cli"].indexOf(a)-["agent_cli"].indexOf(b));
  for(const division of divisions){
    const group=entries.filter(r=>r.mode===division).sort((a,b)=>(a.rank??999)-(b.rank??999)||(b.score??b.reference_score??-1)-(a.score??a.reference_score??-1));
    const section=node("section","division-board"),heading=node("div","division-heading");heading.append(node("span","division-dot"),node("h3","",(divisionNames[division]||division)+(arenaUI.board==="total"?" · 三线总分":"")),node("span","muted small",`${group.length} 位选手`));section.append(heading,buildPodium(group,division,maximum));
    for(const [index,row] of group.entries()){
      const button=node("button",`standing-row${row.rank===1?" is-champion":""}`);button.type="button";button.dataset.modelKey=row.model_key;
      const position=node("span","standing-rank",row.rank===1?"♛":row.rank?String(row.rank).padStart(2,"0"):"—");
      const info=node("span","standing-model");info.append(node("strong","",row.model),node("small","",`${harnessLabel(row.transport)} · ${identityNames[row.model_identity?.source]||"模型来源未记录"} · ${row.matches} 场`));
      if(arenaUI.board==="total")info.append(node("span","standing-breakdown",["text","vision","story"].map(b=>`${{text:"知识",vision:"识图",story:"总结"}[b]} ${row.boards[b]?.score==null?"—":scoreValue(row.boards[b].score)}`).join(" / ")));
      const score=node("span","standing-score");score.append(node("strong","",row.score!=null?scoreValue(row.score):row.reference_score!=null?scoreValue(row.reference_score):"未齐"),node("small","",row.eligible?`/ ${maximum}`:row.stopped?"用户停止 · 不计名次":row.manual?"人工补分 · 不计名次":row.model_identity?.source==="unknown"?"模型未知 · 不计名次":row.model_identity?.source==="mixed"?"模型变化 · 不计名次":arenaUI.board==="total"&&!row.complete?`三榜已齐 ${row.completed_boards}/3`:"参考成绩 · 不计名次"));
      const meter=node("span","standing-meter");meter.style.setProperty("--score",String(100*(row.score??row.reference_score??0)/maximum));button.append(position,modelAvatar(row.model,row.vendor),info,meter,score,node("span","standing-arrow","↗"));
      button.style.setProperty("--order",index);button.addEventListener("click",()=>selectModel(row.model_key));section.append(button);
    }
    $("standings-content").append(section);
  }
  if(!entries.length){const empty=node("div","empty-state arena-empty");empty.append(node("h3","","C 位，虚位以待"),node("p","","当前赛季还没有这条路线的战绩。"));const button=node("button","text-button","去开启一场挑战 →");button.type="button";button.addEventListener("click",()=>setView("dashboard"));empty.append(button);$("standings-content").append(buildPodium([], "agent_cli",maximum),empty);}
  observeNovelContent();
}
function renderRoster() {
  if(!arenaUI.data)return;$("model-roster").replaceChildren();
  const models=new Map();for(const row of arenaUI.data.records)if(!models.has(row.model_key))models.set(row.model_key,row);
  const all=node("button","roster-chip"+(!arenaUI.model?" selected":""),"全部选手");all.type="button";all.addEventListener("click",()=>selectModel(null));$("model-roster").append(all);
  for(const row of models.values()){
    const button=node("button","roster-chip"+(arenaUI.model===row.model_key?" selected":""));button.type="button";
    button.append(modelAvatar(row.model,row.vendor),node("span","",row.model),node("small","",harnessLabel(row.transport)));button.addEventListener("click",()=>selectModel(row.model_key));$("model-roster").append(button);
  }
}
function renderArchive() {
  if(!arenaUI.data)return;
  arenaUI.pageSize=matchMedia("(max-width: 600px)").matches?6:12;
  const selected=arenaUI.data.records.find(r=>r.model_key===arenaUI.model);
  $("history-title").textContent=selected?`${selected.model} 的战绩`:"全部评测记录";
  const search=$("history-search").value.trim().toLocaleLowerCase(),state=$("history-state").value;
  let rows=arenaUI.data.records.filter(r=>(!arenaUI.model||r.model_key===arenaUI.model)&&(!search||r.model.toLocaleLowerCase().includes(search))&&($("history-vendor").value==="all"||r.vendor===$("history-vendor").value));
  rows=rows.filter(r=>state==="all"||(state==="manual"?r.manual:state==="mock_demo"?r.mode==="mock_demo":state==="complete"?r.status==="complete"&&!r.manual:r.status!=="complete"));
  const score=r=>{const value=arenaUI.board==="total"?r.total:r.boards[arenaUI.board];return value?.score??value?.reference_score??-1;};
  const scale=arenaUI.board==="total"?3:1;
  const range=$("history-range").value;
  rows=rows.filter(r=>range==="all"||(range==="high"?score(r)>=90*scale:range==="middle"?score(r)>=60*scale&&score(r)<90*scale:range==="low"?score(r)>=0&&score(r)<60*scale:score(r)<0));
  rows.sort((a,b)=>$("history-sort").value==="score"?score(b)-score(a)||b.scored_at.localeCompare(a.scored_at):$("history-sort").value==="model"?a.model.localeCompare(b.model):$("history-sort").value==="vendor"?a.vendor.localeCompare(b.vendor):b.scored_at.localeCompare(a.scored_at));
  const pages=Math.max(1,Math.ceil(rows.length/arenaUI.pageSize));arenaUI.page=Math.min(arenaUI.page,pages);
  $("history-subtitle").textContent=`${rows.length} 份存档`;
  $("history-page").textContent=`${arenaUI.page} / ${pages}`;$("history-prev").disabled=arenaUI.page<=1;$("history-next").disabled=arenaUI.page>=pages;
  $("history-list").replaceChildren();
  for(const row of rows.slice((arenaUI.page-1)*arenaUI.pageSize,arenaUI.page*arenaUI.pageSize)){
    const card=node("button","record-card");card.type="button";card.dataset.runId=row.run_id;
    const top=node("span","record-top"),name=node("span","record-name");name.append(node("strong","",row.model),node("small","",row.vendor+" · "+harnessLabel(row.transport)));top.append(modelAvatar(row.model,row.vendor),name,node("span","record-open","↗"));
    const scores=node("span","record-scores");for(const board of ["text","vision","story"]){const value=row.boards[board],pill=node("span");pill.append(node("small","",labels[board]),node("strong","",value?scoreValue(value.score??value.reference_score):"—"));if(value&&!value.complete)pill.append(node("em","",value.reference_kind==="confirmed_lower_bound"?"下界":"参考"));scores.append(pill);}
    const bottom=node("span","record-bottom");bottom.append(node("span","",formatDate(row.scored_at)),node("span","record-state",row.stopped?"用户停止 · 保留记录":row.manual?"人工补分":row.source_run?"复评存档":row.status==="complete"?"完整成绩":"未完成"));
    const total=node("span","record-total");total.append(node("small","","本场总分"),node("strong","",row.total?.complete?scoreValue(row.total.score):"三榜未齐"),node("small","",row.total?.complete?"/ 300":"不计总排名"));
    card.append(top,total,scores,node("span","record-dataset",row.dataset_label+(row.excluded_from_season?" · 已移出本赛季":"")),node("span","record-identity",identityNames[row.model_identity?.source]||"模型来源未记录"),bottom);
    card.addEventListener("click",async()=>{try{showResult(await api(`/api/results/${encodeURIComponent(row.run_id)}`));setView("dashboard");$("results-panel").scrollIntoView({behavior:"smooth",block:"start"});}catch(exc){error(exc.message);}});$("history-list").append(card);
  }
  if(!rows.length)$("history-list").append(node("p","empty-state","没有匹配的战绩。试试其他模型或筛选条件。"));
  observeNovelContent();
}
function initArenaUI() {
  document.body.dataset.view="dashboard";
  for(const button of document.querySelectorAll("[data-close]"))button.addEventListener("click",()=>closeDialog(button.dataset.close));
  for(const dialog of document.querySelectorAll("dialog"))dialog.addEventListener("click",event=>{if(event.target===dialog){const box=dialog.getBoundingClientRect();if(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom)dialog.close();}});
  $("question-dialog").addEventListener("close",()=>{if(arenaUI.lastFocus?.isConnected)arenaUI.lastFocus.focus({preventScroll:true});});
  $("open-config-top").addEventListener("click",()=>openConfig());for(const button of document.querySelectorAll("[data-config-role]"))button.addEventListener("click",()=>openConfig(button.dataset.configRole));
  $("finish-config").addEventListener("click",()=>{updateControls();saveSettings();error("");closeDialog("config-dialog");showToast("配置已更新，出场吧 ✦");});
  for(const button of document.querySelectorAll("[data-back-results]"))button.addEventListener("click",()=>{setView("dashboard");$("results-panel").scrollIntoView({behavior:"smooth",block:"start"});});
  for(const button of document.querySelectorAll("[data-rank-board]"))button.addEventListener("click",()=>{arenaUI.board=button.dataset.rankBoard;for(const b of document.querySelectorAll("[data-rank-board]"))b.classList.toggle("selected",b===button);renderStandings();renderArchive();});
  for(const id of ["rank-profile","rank-mode"])$(id).addEventListener("change",renderStandings);
  for(const id of ["history-vendor","history-state","history-range","history-sort"])$(id).addEventListener("change",()=>{arenaUI.page=1;renderArchive();});
  $("history-search").addEventListener("input",()=>{arenaUI.page=1;renderArchive();});
  $("all-records").addEventListener("click",()=>selectModel(null));
  matchMedia("(max-width: 600px)").addEventListener("change",()=>{arenaUI.page=1;renderArchive();});
  $("history-prev").addEventListener("click",()=>{arenaUI.page=Math.max(1,arenaUI.page-1);renderArchive();});$("history-next").addEventListener("click",()=>{arenaUI.page++;renderArchive();});
  $("clear-rankings").addEventListener("click",()=>$("reset-dialog").showModal());
  async function reset(action){
    const button=action==="clear"?$("confirm-clear-rankings"):$("restore-rankings");button.disabled=true;
    try{await api("/api/arena/reset",{method:"POST",headers:{"Content-Type":"application/json","X-Galbench-Token":bootstrap.token},body:JSON.stringify({action})});closeDialog("reset-dialog");await loadArena();showToast(action==="clear"?"新赛季开启 · 原始战绩仍在战绩馆":"历史排名已恢复");}catch(exc){closeDialog("reset-dialog");error(exc.message);}finally{button.disabled=false;}
  }
  $("confirm-clear-rankings").addEventListener("click",()=>reset("clear"));$("restore-rankings").addEventListener("click",()=>reset("restore"));
}
