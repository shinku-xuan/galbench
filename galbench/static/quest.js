"use strict";

// The scene is decorative. Only server counters move the traveler; time changes dialogue only.
const questUI = {job:null, id:null, board:null, manual:false, paused:false, context:null, contextAt:0,
  phase:null, lastProgress:0, cheerTimer:null, arrivalTimer:null,lastIssues:0,issueUntil:0,issueSignature:null,issueTimer:null};
const questRoutes = {
  text:{chapter:"01",name:"记忆森林",icon:"✧",action:"迎战疑问史莱姆",prop:"疑问史莱姆出没",lines:[
    "先别快进，这句话可能是伏笔。", "人物关系，绝不能选错支线。", "BGM 响起，记忆开始读档。", "正在世界观里寻找那块关键拼图。"]},
  vision:{chapter:"02",name:"放学后的相遇",icon:"♡",action:"递出一封心动答卷",prop:"这次相遇，你还记得吗？",lines:[
    "立绘已出现，心动选项加载中。", "这次相遇，能叫出你的名字吗？", "发色只是线索，记忆才是答案。", "别急着选，认真看一眼她的故事。"]},
  story:{chapter:"03",name:"故事装订室",icon:"▤",action:"收集散落的剧情页",prop:"把故事，整理成一本书",lines:[
    "把散落的对话，拼回完整的故事。", "这段独白里，藏着谁的心事？", "正在把长篇日常整理成剧情梗概。", "整理人物、转折，还有没说出口的那句话。"]}
};
function questText(id, text) {const el=$(id);if(el.textContent!==text)el.textContent=text;}
function questRatio(done,total) {return total>0?Math.max(0,Math.min(1,(Number(done)||0)/total)):0;}
function questPhase(job) {
  if(job.state==="stopping")return "stopping";
  if(job.state==="stopped")return "stopped";
  if(job.state==="disconnected")return "offline";
  if(job.state==="failed")return "failed";
  if(job.state==="finished")return job.result?.summary.status==="complete"?"finished":"review";
  return job.generation_wall_seconds!=null || (job.target_total>0&&job.target_done>=job.target_total) || /判分|补判/.test(job.phase||"")?"grading":"answering";
}
function questBoardState(p, job) {
  if(job.state==="stopped")return "已停止 · 保留记录";
  if(job.state==="stopping")return "保存中";
  if(job.state==="finished")return "记录就绪";
  if(p.scored>=p.score_total&&p.score_total>0)return "已处理";
  if(p.scored>0)return "阅卷中";
  if(p.target_done>=p.target_total&&p.target_total>0)return "等待阅卷";
  return p.target_done>0 || (job.active_calls||[]).some(c=>c.board===p.board)?"攻略中":"待出发";
}
function initQuestUI() {
  $("quest-error-hud").addEventListener("click",()=>{
    if($("quest-issues").hidden){showToast("尚未记录调用异常；答错和主动停止不计入报错。");return;}
    $("quest-issues").open=true;$("quest-issues").scrollIntoView({behavior:"smooth",block:"center"});
  });
  $("quest-motion").addEventListener("click",()=>{
    questUI.paused=!questUI.paused;syncQuestMotion();
  });
  $("quest-cheer").addEventListener("click",()=>{
    if(!questUI.job||questUI.job.state!=="running")return;
    const burst=$("quest-cheer-burst");burst.classList.remove("is-cheering");
    void burst.offsetWidth;burst.classList.add("is-cheering");
    clearTimeout(questUI.cheerTimer);questUI.cheerTimer=setTimeout(()=>burst.classList.remove("is-cheering"),1700);
  });
  document.addEventListener("visibilitychange",syncQuestMotion);
  matchMedia("(prefers-reduced-motion: reduce)").addEventListener("change",syncQuestMotion);
  matchMedia("(max-width: 600px)").addEventListener("change",()=>{if(questUI.job)renderQuest(questUI.job);});
  syncQuestMotion();
}
function syncQuestMotion() {
  const reduced=matchMedia("(prefers-reduced-motion: reduce)").matches;
  $("quest-theater").dataset.paused=String(questUI.paused||document.hidden||reduced);
  $("progress-panel").dataset.paused=$("quest-theater").dataset.paused;
  const button=$("quest-motion");button.disabled=reduced;
  button.setAttribute("aria-pressed",String(questUI.paused||reduced));
  button.setAttribute("aria-label",reduced?"已跟随系统减少动态效果":questUI.paused?"继续播放小剧场动画":"暂停小剧场动画");
  button.title=reduced?"跟随系统减少动态效果，实际进度照常更新":questUI.paused?"继续动画，测试进度不受影响":"暂停动画，测试照常进行";
  button.textContent=reduced?"◇":questUI.paused?"▷":"Ⅱ";
}
function createQuestRoutes(job) {
  $("route-progress").replaceChildren();
  for(const board of Object.keys(job.board_progress||{})) {
    if(!questRoutes[board])continue;
    const route=questRoutes[board],button=node("button",`quest-route quest-route-${board}`);button.type="button";button.dataset.questBoard=board;
    const icon=node("span","quest-route-icon",route.icon),copy=node("span","quest-route-copy");
    copy.append(node("strong","",labels[board]),node("small","quest-route-state","待出发"));
    const seals=node("span","quest-seals");seals.setAttribute("aria-hidden","true");
    for(let i=0;i<5;i++)seals.append(node("i","","◆"));
    button.append(icon,copy,seals);button.addEventListener("click",()=>{
      questUI.manual=true;questUI.board=board;questUI.context=null;renderQuest(questUI.job);
    });$("route-progress").append(button);
  }
  const follow=node("button","quest-follow","跟随进度 ↗");follow.id="quest-follow";follow.type="button";
  follow.addEventListener("click",()=>{questUI.manual=false;questUI.context=null;renderQuest(questUI.job);});$("route-progress").append(follow);
}
function renderQuest(job) {
  if(!job)return;
  const boards=Object.keys(job.board_progress||{}).filter(b=>questRoutes[b]);
  if(!boards.length)return;
  if(questUI.id!==job.id) {
    questUI.id=job.id;questUI.board=null;questUI.manual=false;questUI.context=null;questUI.contextAt=0;questUI.phase=null;questUI.lastProgress=0;
    questUI.lastIssues=0;questUI.issueUntil=0;questUI.issueSignature=null;
    $("quest-issues").open=false;$("quest-theater").classList.remove("quest-error-hit");
    clearTimeout(questUI.arrivalTimer);$("quest-theater").classList.remove("quest-arrival");
    $("quest-cheer-burst").classList.remove("is-cheering");createQuestRoutes(job);
  }
  questUI.job=job;
  const phase=questPhase(job),grading=["grading","finished","review"].includes(phase);
  const active=(grading?job.active_grades:job.active_calls)||[];
  const currentActive=active.some(c=>c.board===questUI.board)?questUI.board:active.find(c=>boards.includes(c.board))?.board;
  const preserve=["stopping","stopped","offline","failed"].includes(phase)&&boards.includes(questUI.board);
  const candidate=preserve?questUI.board:currentActive||boards.find(b=>{const p=job.board_progress[b];return grading?p.scored<p.score_total:p.target_done<p.target_total;})||boards.at(-1);
  const previousBoard=questUI.board;
  if(!questUI.manual||!boards.includes(questUI.board))questUI.board=candidate;
  const board=questUI.board,p=job.board_progress[board],route=questRoutes[board],theater=$("quest-theater");
  if(previousBoard!==board){questUI.context=null;questUI.contextAt=0;}
  theater.dataset.board=board;theater.dataset.phase=phase;
  $("progress-panel").dataset.phase=phase;
  renderQuestIssues(job);
  const stop=$("stop-run");stop.disabled=stopRequestPending||!["running","disconnected"].includes(job.state);
  stop.textContent=stopRequestPending?"正在请求停止…":phase==="stopping"?"停止并保存中…":phase==="stopped"?"已停止":job.state==="finished"?"本轮已结束":"■ 停止测试";
  const answer=questRatio(p.target_done,p.target_total),scored=questRatio(p.scored,p.score_total);
  // Do not animate a made-up percentage or assume a completed request was correct.
  const compact=matchMedia("(max-width: 600px)").matches;
  const position=compact?350+185*answer:230+315*answer;$("quest-traveler").setAttribute("transform",`translate(${position} 0)`);
  const processed=boards.reduce((s,b)=>s+(job.board_progress[b].target_done||0)+(job.board_progress[b].scored||0),0);
  if(questUI.phase===phase&&processed>questUI.lastProgress&&job.state==="running") {
    theater.classList.remove("quest-arrival");void theater.offsetWidth;theater.classList.add("quest-arrival");
    clearTimeout(questUI.arrivalTimer);questUI.arrivalTimer=setTimeout(()=>theater.classList.remove("quest-arrival"),850);
  }
  questUI.lastProgress=processed;questUI.phase=phase;
  questText("quest-location",route.name);
  questText("quest-stage-note",phase==="stopping"?"停止派发 · 正在保留答卷":phase==="stopped"?"本轮由测试者停止":phase==="answering"?`本线答题进度 ${Math.round(100*answer)}%`:phase==="grading"?`本线判分处理 ${Math.round(100*scored)}%`:phase==="finished"?"本次旅程已结算":phase==="review"?"存档已保留 · 仍有题目待核验":phase==="failed"?"本次运行未完成":"等待重新连接故事");
  questText("quest-prop-label",phase==="grading"?"裁判阅卷中":phase==="finished"?"下一场故事，等你开启":phase==="review"?"请在结果中查看异常与补分":phase==="answering"?route.prop:"原始记录仍保留在本地");
  questText("quest-actor-label",phase==="answering"?"模型选手 · 攻略中":phase==="grading"?"答题阶段结束 · 正在结算":phase==="finished"?"模型选手 · 本回合结束":phase==="review"?"旅途有小插曲 · 等你核验":"先暂停一下，别丢下存档");
  if(phase==="stopping"||phase==="stopped") {
    questText("quest-actor-label",phase==="stopping"?"停止派发 · 保存答卷":"由测试者停止 · 记录已保留");
    questText("quest-prop-label",phase==="stopping"?"正在整理本地记录":job.result?"现有成绩可在下方查看":"本轮尚未生成答卷");
  }
  questText("quest-phase-badge",{answering:"攻略中",grading:"判分中",finished:"已完成",review:"待核验",failed:"运行中断",offline:"连接断开",stopping:"保存中",stopped:"已停止"}[phase]);
  for(const button of $("route-progress").querySelectorAll("[data-quest-board]")) {
    const b=button.dataset.questBoard,progress=job.board_progress[b],selected=b===board;
    button.classList.toggle("is-selected",selected);button.setAttribute("aria-pressed",String(selected));
    button.querySelector(".quest-route-state").textContent=questBoardState({...progress,board:b},job);
    const pending=job.result?.summary.boards?.[b]?.pending_samples||0;
    if(job.state==="finished"&&pending>0)button.querySelector(".quest-route-state").textContent=`${pending} 题待核验`;
    const fraction=questRatio(progress.target_done,progress.target_total)*.7+questRatio(progress.scored,progress.score_total)*.3;
    [...button.querySelectorAll(".quest-seals i")].forEach((seal,i)=>seal.classList.toggle("is-lit",fraction>=(i+1)/5));
    button.title=`${labels[b]}：答题处理 ${progress.target_done}/${progress.target_total}，判分处理 ${progress.scored}/${progress.score_total}。点击切换小剧场`;
    button.setAttribute("aria-label",button.title);
  }
  $("quest-follow").classList.toggle("is-following",!questUI.manual);$("quest-follow").setAttribute("aria-pressed",String(!questUI.manual));
  $("quest-cheer").disabled=job.state!=="running";
  questText("quest-sync-note",phase==="stopping"?"不再发起新调用 · 正在整理已有记录":phase==="stopped"?"已停止的题目不计入报错，也不按答错计零分":job.state==="running"?(questUI.manual?"自由观看中 · 点击「跟随进度」返回当前路线":"舞台随实际答题进度推进"):phase==="finished"?"答题与判分记录已保存":phase==="review"?"参考成绩已生成 · 请查看待核验题目":"已暂停舞台 · 请查看运行详情");
  tickQuest();syncQuestMotion();
  renderQuestionMemory(job);
}
function renderQuestIssues(job) {
  const issues=job.issues||job.result?.summary.operations?.runtime_issues||{total:0,target:0,judge:0,warnings:0,system:0,recent:[]};
  const total=issues.total||0,recent=issues.recent||[],latest=recent[0],offline=job.state==="disconnected";
  $("quest-theater").dataset.hasErrors=String(total>0);$("quest-error-hud").classList.toggle("has-errors",total>0||offline);
  questText("quest-error-symbol",total>0||offline?"!":"✓");questText("quest-error-total",`${offline?"已知报错":"累计报错"} ${total} 次`);
  questText("quest-error-breakdown",offline?"本地连接中断，统计等待重新连接":total?`答题 ${issues.target||0} · 裁判 ${issues.judge||0} · 告警 ${issues.warnings||0}${issues.system?` · 本地 ${issues.system}`:""}`:"旅途顺利，尚未发现调用异常");
  if(total>questUI.lastIssues&&latest&&Date.now()-Date.parse(latest.at)<10000&&job.state==="running") {
    questUI.issueUntil=Date.now()+6500;const theater=$("quest-theater");theater.classList.remove("quest-error-hit");void theater.offsetWidth;theater.classList.add("quest-error-hit");
    clearTimeout(questUI.issueTimer);questUI.issueTimer=setTimeout(()=>theater.classList.remove("quest-error-hit"),1600);
  }
  questUI.lastIssues=total;$("quest-issues").hidden=!total;questText("quest-issues-title",`异常记录 · 累计 ${total} 次`);
  const signature=JSON.stringify([job.id,total,recent]);if(signature===questUI.issueSignature)return;questUI.issueSignature=signature;
  $("quest-issues-list").replaceChildren(...recent.map(issue=>{
    const row=node("div",`quest-issue-row issue-${issue.kind}`),copy=node("span");
    copy.append(node("strong","",issue.message||"调用异常"),node("small","",`${issue.question_id||"本地运行"}${issue.question_id?` · 第 ${(issue.repeat||0)+1} 轮`:""} · ${{target:"答题",judge:"裁判",system:"本地"}[issue.source]||"运行"} · ${issue.code||"unknown"}`));
    row.append(node("i","",issue.kind==="warning"?"↻":"!"),copy,node("span","quest-issue-number",`#${issue.sequence}`));return row;
  }));
}
function tickQuest() {
  const job=questUI.job;if(!job)return;
  const phase=questPhase(job),board=questUI.board,route=questRoutes[board];if(!route)return;
  const turn=Math.floor(Math.max(0,Date.now()-Date.parse(job.created_at))/8000);
  let title,line,speaker;
  if(phase==="stopping") {
    title="正在收起这一回合的答卷";line="已取消后续请求，正在中止本地调用并整理已有记录。";speaker="存档进行中";
  } else if(phase==="stopped") {
    title="本次攻略已停止";line=job.result?"已有答卷和可用成绩已保存，未完成题目可继续人工核验。":"准备阶段已停止，本轮尚未生成答卷。";speaker="由测试者停止";
  } else if(["answering","grading"].includes(phase)&&Date.now()<questUI.issueUntil&&job.issues?.recent?.[0]) {
    const issue=job.issues.recent[0];title=issue.kind==="warning"?"遇到小插曲，已记录重试告警":"捕获一个调用异常，已记入存档";
    line=`${issue.question_id||"本地运行"} · ${issue.message}。${issue.kind==="warning"?"等待本次调用继续返回。":"其余题目会继续处理，可随时停止测试。"}`;speaker="异常观测站";
  } else if(phase==="answering") {
    const active=(job.active_calls||[]).filter(c=>c.board===board),now=Date.now();
    const stillActive=questUI.context&&active.some(c=>c.work_title===questUI.context.work_title);
    // Keep a work caption while that work is active, without flickering between fast requests.
    if(!stillActive&&(now-questUI.contextAt>=8000||!questUI.contextAt)) {
      questUI.context=active.find(c=>c.work_title)||null;questUI.contextAt=now;
    }
    else if(!stillActive)questUI.context=null;
    const work=questUI.context?.work_title;
    title=work?`正在攻略《${work}》`:route.action;
    line=route.lines[turn%route.lines.length];speaker="攻略手记";
  } else if(phase==="grading") {
    title=/补判/.test(job.phase||"")?"阅卷遇到小插曲，正在补判":"这封答卷，裁判正在认真读";
    line=["心动是气氛，得分要看证据。","正在核对评分记录，固定答案由规则直接判分。","让每一份答卷，都找到有依据的分数。"][turn%3];speaker="阅卷室来信";
  } else if(phase==="finished") {
    title="本回合结束，收下你的成绩单";line="每一条攻略线，都留下了属于这个模型的答案。";speaker="存档完成";
  } else if(phase==="review") {
    title="故事已存档，还有片段等你核验";line="参考成绩已生成；待核验题目可在「异常与人工补分」中处理。";speaker="存档提醒";
  } else {
    title=phase==="offline"?"和舞台的连接，暂时中断了":"这一回合，遇到了一点小插曲";
    line=phase==="offline"?"请确认启动窗口仍在运行；连接恢复后刷新页面，重新接上旅程。":"已生成的回答仍保留，请展开比赛详情检查原因。";speaker="暂停手记";
  }
  questText("quest-title",title);questText("quest-line",line);questText("quest-speaker",speaker);
}
function questDisconnected() {if(questUI.job)renderQuest({...questUI.job,state:"disconnected"});}
