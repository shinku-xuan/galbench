"use strict";
const $ = (id) => document.getElementById(id);
const labels = {text:"游戏内知识", vision:"角色识图", story:"剧情总结"};
const developerNames = {Yuzusoft:"柚子社 Yuzusoft",AUGUST:"八月社 AUGUST",Makura:"枕社 Makura",FAVORITE:"FAVORITE",Navel:"Navel",Key:"Key",minori:"minori（中二社）",GIGA:"戏画 GIGA","ASa Project":"颜艺社 ASa Project","Azarashi Soft":"海豹社 Azarashi Soft"};
const statuses = {C:"全对",P:"部分正确",W:"答错",A:"放弃",V:"无效回答",HOLD:"待复核",ERROR:"调用失败"};
const states = {complete:"评分完成",needs_review:"判分未完成",incomplete:"答题调用失败",stopped:"已停止 · 保留现有成绩"};
const resultStates = {scored:"评分完成",scored_with_manual:"已人工补分 · 保留异常记录",execution_failed:"调用失败",execution_incomplete:"调用未完成",judge_failed:"判分失败",needs_review:"待复核",topic_only:"类别未齐",not_called:"未调用",cancelled:"用户停止 · 未完成",stopped:"已停止 · 保留现有成绩"};
const savedFields = ["dataset-profile","target-vision","judge-enabled","repeats","concurrency",
  ...["target","judge"].flatMap(role=>["provider","executable","cli-model","cli-timeout","use-client-config","cli-profile","model-label","credential-env"].map(field=>role+"-"+field))];
const preferencesKey = "galbench.preferences.v1.1";
let bootstrap, mode = "agent_cli", busy = false, currentResult = null, currentRun = null, cursor = 0, startedAt = 0, timer;
let liveJob = null, bankPage = 1, bankPages = 1, bankSequence = 0, bankLoaded = false, bankDebounce;
let stopRequestPending = false;
let executableSelections={target:{},judge:{}};
let modelSelections={target:{},judge:{}};

function node(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
}
function error(message) {
  $("global-error").textContent = message;
  $("global-error").hidden = !message;
  if (message) {
    if(document.querySelector("dialog[open]"))showToast(message);
    else $("global-error").scrollIntoView({behavior:"smooth", block:"center"});
  }
}
async function api(path, options = {}) {
  const res = await fetch(path, {cache:"no-store", ...options});
  const data = await res.json();
  if (!res.ok) {
    const error=new Error(data.error || "请求未能完成");error.code=data.error_code;error.details=data.error_details;throw error;
  }
  return data;
}
function saveSettings() {
  for(const role of ["target","judge"])if($(role+"-cli-model").dataset.harness)rememberModel(role);
  const fields = {};
  for (const id of savedFields) fields[id] = $(id).type === "checkbox" ? $(id).checked : $(id).value;
  const boards = [...document.querySelectorAll("input[name=board]:checked")].map(el => el.value);
  try { localStorage.setItem(preferencesKey, JSON.stringify({fields, boards, mode, paths:executableSelections, models:modelSelections})); } catch (_) { /* Optional persistence. */ }
}
function restoreSettings() {
  try {
    const current = localStorage.getItem(preferencesKey);
    const saved = JSON.parse(current || localStorage.getItem("galbench.preferences.v1"));
    if (!saved) return;
    for (const id of savedFields) {
      if (!Object.hasOwn(saved.fields || {}, id)) continue;
      if ($(id).type === "checkbox" && typeof saved.fields[id] === "boolean") $(id).checked = saved.fields[id];
      else if ($(id).type !== "checkbox" && typeof saved.fields[id] === "string") $(id).value = saved.fields[id];
    }
    for(const role of ["target","judge"])for(const agent of bootstrap.agents||[]){
      const model=saved.models?.[role]?.[agent.id];
      if(typeof model==="string")modelSelections[role][agent.id]=model;
      const choice=saved.paths?.[role]?.[agent.id];
      if(choice&&typeof choice.path==="string")executableSelections[role][agent.id]={path:choice.path,source:choice.source==="auto"?"auto":"manual"};
    }
    if (Array.isArray(saved.boards)) for (const el of document.querySelectorAll("input[name=board]")) el.checked = saved.boards.includes(el.value);
    for (const role of ["target","judge"]) if(saved.fields?.[role+"-provider"]==="codex"&&!Object.hasOwn(saved.fields,role+"-use-client-config")) {
      $(role+"-use-client-config").checked=false; // Preserve older Codex runs' isolated routing.
    }
    for (const role of ["target","judge"]) if (!(bootstrap.agents||[]).some(agent=>agent.id===$(role+"-provider").value)) {
      $(role+"-provider").value="codex";
      $(role+"-executable").value=""; $(role+"-cli-model").value="";
      if(role==="target")$("target-vision").checked=false;
    }
    if (saved.mode === "mock_demo") mode = "mock_demo";
    if(["target","judge"].some(role=>saved.fields?.[role+"-provider"]==="openai_compatible"))$("preferences-notice").hidden=false;

  } catch (_) { /* A damaged browser preference must not prevent startup. */ }
}
function setMode(next) {
  mode = next;
  for (const [id, selected] of [["mode-agent",mode === "agent_cli"],["mode-demo",mode === "mock_demo"]]) {
    $(id).classList.toggle("selected", selected);
    $(id).setAttribute("aria-pressed", String(selected));
  }
  $("agent-settings").hidden = mode === "mock_demo";
  $("demo-notice").hidden = mode !== "mock_demo";
  updateControls();
}
function selectedBoards() { return [...document.querySelectorAll("input[name=board]:checked")].map(el => el.value); }
function selectedDataset() { return bootstrap.datasets.find(d=>d.id === $("dataset-profile").value) || bootstrap.datasets[0]; }
function needsJudge() { return selectedBoards().some(b=>(selectedDataset().boards[b].semantic_questions ?? selectedDataset().boards[b].questions) > 0); }
function isAgent(role) { return true; }
function normalizeCliModel(role) {
  if ($(role+"-provider").value !== "codex") return;
  const input = $(role+"-cli-model"), key = input.value.replace(/[\s_-]+/g, "").toLowerCase();
  const model = (bootstrap.codex_models || []).find(m=>m.id.replace(/[\s_-]+/g, "").toLowerCase() === key);
  if (model && model.id !== input.value) {
    input.value = model.id;
    $(role+"-cli-status").textContent = `已转换为调用 ID：${model.id}（${model.label}）。`;
  }
}
function rememberExecutable(role){
  const input=$(role+"-executable"),provider=input.dataset.harness||$(role+"-provider").value;
  executableSelections[role][provider]={path:input.value.trim(),source:input.dataset.pathSource||"manual"};
}
function rememberModel(role){
  const input=$(role+"-cli-model"),provider=input.dataset.harness||$(role+"-provider").value;
  if(provider)modelSelections[role][provider]=input.value.trim();
}
function fillModel(role){
  const input=$(role+"-cli-model"),provider=$(role+"-provider").value;
  const legacy=!input.dataset.harness?input.value.trim():"";
  input.value=Object.hasOwn(modelSelections[role],provider)?modelSelections[role][provider]:legacy;
  input.dataset.harness=provider;rememberModel(role);
}
function applyDeepSeekTemplate(role){
  if(busy||$(role+"-provider").value!=="deepseek_harness")return;
  $(role+"-cli-model").value="deepseek-official/deepseek-flash";
  rememberModel(role);updateTransport(role);saveSettings();
  $(role+"-cli-status").textContent="已填入真实路线 deepseek-official/deepseek-flash。检查 CLI 后可按你的识图确认进行测试。";
}
function fillExecutable(role,{force=false}={}){
  const input=$(role+"-executable"),provider=$(role+"-provider").value,info=(bootstrap.agents||[]).find(a=>a.id===provider);
  const previous=executableSelections[role][provider];
  // Old preferences did not track auto/manual. Preserve a nonempty saved value.
  const legacy=!input.dataset.harness&&!previous&&input.value.trim();
  const manual=!force&&(previous?.source==="manual"&&previous.path||legacy);
  input.value=manual?(previous?.path||legacy):(info?.executable||"");
  input.dataset.harness=provider;input.dataset.pathSource=manual?"manual":"auto";
  rememberExecutable(role);
  $(role+"-path-source").textContent=manual?"手工指定 · 自动识别不会覆盖":input.value?"已自动填入 · 可手工修改":"未发现独立 CLI · 安装后可重新识别";
}
async function detectCli(role){
  const button=$(role+"-detect-cli");button.disabled=true;
  const provider=$(role+"-provider").value,path=$(role+"-executable").value;
  try{
    const data=await api("/api/agents");bootstrap.agents=data.agents;
    if($(role+"-provider").value!==provider||$(role+"-executable").value!==path)return;
    const found=data.agents.find(a=>a.id===$(role+"-provider").value)?.executable;
    if(found){fillExecutable(role,{force:true});$(role+"-cli-status").textContent="已自动填入 CLI 路径，尚未验证账户或模型。";}
    else $(role+"-cli-status").textContent="未发现独立 CLI，已保留当前手工路径。";
    updateControls();saveSettings();
  }catch(exc){$(role+"-cli-status").textContent=exc.message;}
  finally{button.disabled=busy;}
}
function updateTransport(role) {
  const provider=$(role+"-provider").value,info=(bootstrap.agents||[]).find(a=>a.id===provider);
  const deepseek=provider==="deepseek_harness",model=$(role+"-cli-model").value.trim();
  $(role+"-model-title").textContent=deepseek?"实际模型路线（provider/model）":"模型调用 ID（可留空）";
  $(role+"-model-template").hidden=!deepseek;
  $(role+"-model-route").textContent=model?`本次指定路线：${model}`:deepseek?"当前未指定路线，将使用 ACP 默认模型；不会跟随 Web 界面的模型选择。":"沿用客户端默认模型";
  $(role+"-cli").hidden=false;$(role+"-codex-config").hidden=provider!=="codex";
  $(role+"-cli-profile").disabled=busy||!$(role+"-use-client-config").checked;
  $(role+"-cli-model").disabled=busy||info?.model_override===false;
  $(role+"-cli-model").setAttribute("list",provider==="codex"?"codex-model-ids":"");
  $(role+"-cli-model").placeholder=info?.model_override===false?"在客户端配置模型，本次不覆盖":provider==="deepseek_harness"?"如 deepseek-official/deepseek-flash；留空沿用 ACP":provider==="opencode"||provider==="minimax_code"?"provider/model，留空沿用客户端":"模型调用 ID，可留空";
  let hint=(info?.installed?"已发现 CLI。":"需要先安装官方 CLI。")+(info?.notes||"");
  if(provider==="codex")hint+=$(role+"-use-client-config").checked?" 沿用 config.toml / profile 的模型提供商。":" 忽略用户 config.toml，留空使用 CLI 内置模型。";
  if(provider==="kimi_code"&&role==="target"&&$("target-vision").checked)hint+=" 图片通过新版 ACP；请清空模型覆盖 ID，先在客户端选择视觉模型。ACP 完成依据与工具审计边界会写入答卷。";
  hint+=provider==="deepseek_harness"?" 检查会验证空 ACP 会话和禁用工具的插件；图片声明仅供参考，不发送题目。":" 检查只验证版本、帮助参数和命令配置，不请求模型。";
  hint+=" 额度与计费由客户端所选通道决定。";
  $(role+"-cli-hint").textContent=hint;
  $(role+"-cli-docs").href=info?.docs||"#";
}
async function checkCli(role) {
  const button = $(role+"-check-cli"); button.disabled = true;
  if(!$(role+"-executable").value.trim())fillExecutable(role);
  const settings=config(role);
  $(role+"-cli-status").textContent = "正在检查可执行文件与运行时…";
  try {
    const info = await api("/api/clients/check", {method:"POST", headers:{"Content-Type":"application/json","X-Galbench-Token":bootstrap.token},
      body:JSON.stringify(settings)});
    if($(role+"-provider").value!==settings.provider||$(role+"-executable").value.trim()!==settings.executable||config(role).cli_model!==settings.cli_model)return;
    const checked=info.runtime_check;
    const imageStatus=checked?.agent_capabilities?.promptCapabilities?.image?"ACP 声明支持图片。":settings.supports_vision?"ACP 未声明图片能力；将按你的确认尝试识图，失败仅记录对应题目，可随时停止。":"ACP 未声明图片能力。";
    $(role+"-cli-status").textContent = `已找到 ${info.version || info.provider}。${checked?`ACP 启动及禁用工具验证通过。${imageStatus}`:""}请在客户端确认模型与额度；尚未发送模型请求。`;
  } catch (exc) {
    const diagnostic=exc.details?.stderr_preview?.split("\n").filter(line=>line.trim()&&!line.includes("GALBENCH_")&&!line.trim().startsWith("at ")).slice(0,3).join(" ").slice(0,360);
    $(role+"-cli-status").textContent = exc.message+(diagnostic?` · ${diagnostic}`:"");
  }
  finally { button.disabled = busy; }
}
function updateControls() {
  const boards = selectedBoards(), repeats = Number($("repeats").value) || 1;
  const dataset = selectedDataset();
  const badVision = mode === "agent_cli" && boards.includes("vision") && !$("target-vision").checked;
  const total = boards.reduce((sum,b) => sum + dataset.boards[b].questions, 0);
  const calls = boards.reduce((sum,b) => sum + dataset.boards[b].calls, 0) * repeats;
  for (const [board,label] of Object.entries({text:"文本",vision:"角色识图",story:"总结"})) $(board+"-count").textContent = `${dataset.boards[board].questions} 道${label}题`;
  const coverage = dataset.coverage;
  $("dataset-note").textContent = coverage ? `${coverage.boards.text.works} 作知识题 · ${coverage.boards.vision.works} 作识图题 · ${coverage.boards.story.works} 作总结片段` : "开发题成绩仅供比较。";
  $("dataset-scoring-note").textContent = (coverage?.boards.text.rules_only && coverage?.boards.vision.rules_only ? "知识与识图按固定答案等权计分，总结使用裁判或人工评分。" : "固定题用规则评分，解释与总结按需使用裁判。") + "每榜满分 100，同场三榜总分 300；开发题成绩仅供比较。";
  $("call-estimate").textContent = `${total} 道题 × ${repeats} 轮 / ${calls} 次答题调用`;
  $("vision-warning").hidden = !badVision;
  $("budget-warning").hidden=true;
  $("start-button").disabled = busy || !boards.length || badVision;
  $("start-button").replaceChildren(document.createTextNode(busy ? "测试进行中" : mode === "mock_demo" ? "开始演示" : "开始测试"), node("span","","↗"));
  $("settings-fields").disabled = busy;
  $("run-fields").disabled = busy;
  $("judge-fields").disabled = busy || !$("judge-enabled").checked || (!needsJudge() && !currentResult?.scores.some(r=>r.scoring_state === "judge_failed"));
  $("rescore-button").disabled = busy;
  $("retry-judge-button").disabled = busy;
  for (const role of ["target","judge"]) updateTransport(role);
  $("cost-hint").textContent = mode === "mock_demo" ? "固定模拟回答，不调用外部 API，也不消耗额度。" :
    !needsJudge() ? "固定答案 · 规则直判" : $("judge-enabled").checked ? "固定题规则直判 · 解释与总结按需调用裁判" : "裁判已关闭 · 语义题可能需要人工复核";
  renderPlayerSlots();
  if(document.body.dataset.screen==="title")$("enter-arena").replaceChildren(document.createTextNode(busy?"返回进行中的比赛 ":"进入攻略大厅 "),node("span","","↗"));
}
function config(role) {
  normalizeCliModel(role);
  const provider=$(role+"-provider").value,info=(bootstrap.agents||[]).find(a=>a.id===provider);
  return {provider,executable:$(role+"-executable").value.trim(),cli_model:provider!=="deepseek_harness"&&info?.model_override===false?"":$(role+"-cli-model").value.trim(),
    use_client_config:provider==="codex"&&$(role+"-use-client-config").checked,
    cli_profile:provider==="codex"&&$(role+"-use-client-config").checked?$(role+"-cli-profile").value.trim():"",
    model_label:$(role+"-model-label").value.trim(),credential_env:$(role+"-credential-env").value.trim(),
    supports_vision:role==="target"&&$("target-vision").checked,timeout_seconds:Number($(role+"-cli-timeout").value)};
}
function setBusy(value) { busy = value; updateControls(); }
async function start() {
  if (busy) return;
  error("");
  const payload = {mode, dataset_profile:selectedDataset().id, boards:selectedBoards(), repeats:Number($("repeats").value), concurrency:Number($("concurrency").value),
    judge_enabled:$("judge-enabled").checked && needsJudge()};
  if (mode === "agent_cli") {
    payload.target = config("target"); payload.judge = config("judge");
  }
  saveSettings(); setBusy(true);
  try {
    const job = await api("/api/runs", {method:"POST", headers:{"Content-Type":"application/json","X-Galbench-Token":bootstrap.token}, body:JSON.stringify(payload)});
    for (const role of ["target","judge"]) if (typeof job.cli_models?.[role] === "string") {$(role+"-cli-model").value = job.cli_models[role];rememberModel(role);}
    saveSettings();
    currentRun = job.id; cursor = 0; $("run-log").textContent = "";
    $("stop-run").disabled=false;$("stop-run").textContent="■ 停止测试";
    $("progress-panel").hidden = false; $("progress-panel").classList.remove("done");
    $("result-content").hidden = true; $("empty-results").hidden = false;
    $("empty-results").querySelector("h3").textContent = "正在写入新的存档";
    $("try-demo").hidden = true;
    $("progress-panel").scrollIntoView({behavior:"smooth",block:"nearest"});
    await poll();
  } catch (exc) { setBusy(false);error(exc.message); }
}
function updateClock() {
  if (!startedAt) return;
  const seconds = Math.max(0, Math.floor((Date.now()-startedAt)/1000));
  $("elapsed").textContent = `${String(Math.floor(seconds/60)).padStart(2,"0")}:${String(seconds%60).padStart(2,"0")}`;
  for (const el of document.querySelectorAll("[data-started-at]")) el.textContent = duration((Date.now()-Date.parse(el.dataset.startedAt))/1000);
  if (liveJob) $("answer-phase-time").textContent = `答题阶段用时 ${duration(liveJob.generation_wall_seconds ?? (Date.now()-Date.parse(liveJob.created_at))/1000)}${liveJob.generation_wall_seconds == null ? " · 计时中" : " · 已结束"}`;
  tickQuest();
}
function duration(seconds) { return typeof seconds === "number" && Number.isFinite(seconds) ? `${Math.max(0,seconds).toFixed(1)} 秒` : "未记录"; }
function renderCalls(job) {
  $("active-calls").replaceChildren();
  for (const call of job.active_calls || []) {
    const line = node("div","call-item");
    const clock = node("strong","",duration((Date.now()-Date.parse(call.started_at))/1000)); clock.dataset.startedAt = call.started_at;
    line.append(node("span","",`${call.question_id} · 第 ${call.repeat+1} 轮 · 回答中`),clock); $("active-calls").append(line);
  }
  $("recent-calls").replaceChildren(...(job.recent_calls || []).slice(0,4).map(call=>node("div","call-item",`${call.question_id} · 第 ${call.repeat+1} 轮 · ${call.state === "ok" ? "已回答" : call.state === "cancelled" ? "已停止" : "调用失败"} · ${duration(call.elapsed_seconds)}`)));
}
async function stopCurrentRun() {
  if(!currentRun||stopRequestPending)return;
  stopRequestPending=true;$("stop-run").disabled=true;$("stop-run").textContent="正在请求停止…";
  try {
    const reply=await api("/api/runs/stop",{method:"POST",headers:{"Content-Type":"application/json","X-Galbench-Token":bootstrap.token},body:JSON.stringify({run_id:currentRun})});
    if(reply.state==="stopping"&&liveJob?.id===currentRun){liveJob={...liveJob,state:"stopping",stop_requested:true};renderQuest(liveJob);}
    showToast(reply.state==="stopping"?"正在停止调用，保存已有答卷…":"本轮已经结束，现有记录已保留。");
  } catch(exc){error(exc.message);}
  finally {stopRequestPending=false;if(questUI.job)renderQuest(questUI.job);}
}
async function poll() {
  try {
    const job = await api(`/api/runs/${encodeURIComponent(currentRun)}?after=${cursor}`);
    liveJob = job; renderCalls(job);
    renderQuest(job);
    startedAt = Date.parse(job.created_at); updateClock();
    if (!timer) timer = setInterval(updateClock,1000);
    cursor = job.cursor;
    if (job.logs.length) {
      $("run-log").textContent += job.logs.join("\n") + "\n";
      $("run-log").scrollTop = $("run-log").scrollHeight;
    }
    $("progress-panel").hidden = false;
    $("phase-label").textContent = job.phase;
    $("target-progress").textContent = `答题 ${job.target_done} / ${job.target_total}`;
    $("score-progress").textContent = `判分 ${job.scored} / ${job.score_total}`;
    $("run-progress").value = 70*job.target_done/job.target_total + 30*job.scored/job.score_total;
    if (["running","stopping"].includes(job.state)) {
      $("running-note").textContent = job.state==="stopping"?"正在取消后续请求并保存已有记录。不会再发起新的模型调用；已提交服务商的请求仍可能计费。":"测试期间请保持启动窗口运行。网页刷新后会重新连接当前任务。";
      setTimeout(poll,1000);
    } else {
      clearInterval(timer); timer = null; setBusy(false); $("progress-panel").classList.add("done");
      if (job.state === "finished") {
        $("run-progress").value = 100;
        $("phase-label").textContent = resultStates[job.result.summary.result_state] || states[job.result_status] || "测试完成";
        $("running-note").textContent = "本次成绩已保存。点击结果中的「查看错题」回看未满分回答，或修改配置再运行一次。";
        showResult(job.result); celebrateFinish(); await loadHistory();
      } else if(job.state==="stopped") {
        $("phase-label").textContent="已停止测试";$("running-note").textContent=job.result?"已有答卷和可用成绩已保存；未完成题目可人工补分或用原存档续跑。":"准备阶段已停止，本轮尚未生成答卷。";
        if(job.result){
          showResult(job.result);
          const ops=job.result.summary.operations,known=Object.values(job.result.summary.boards).reduce((s,b)=>s+(b.scored_samples||0),0);
          $("run-progress").value=70*(ops.completed_target_calls||0)/job.target_total+30*known/job.score_total;
        }else{$("empty-results").querySelector("h3").textContent="本轮已停止，尚未生成答卷";$("try-demo").hidden=false;}
        showToast(job.result?"已停止，已有答卷与成绩已存档。":"准备阶段已停止。");await loadHistory();
      } else { error(job.error); $("running-note").textContent = job.error; }
    }
  } catch (_) {
    clearInterval(timer); timer = null; setBusy(false);
    questDisconnected();
    error("无法连接本地服务。请确认启动窗口仍在运行，恢复服务后刷新页面查看存档。");
  }
}
function formatDate(value) { const date = new Date(value); return Number.isNaN(date.getTime()) ? "" : date.toLocaleString("zh-CN", {hour12:false}); }
function scoreValue(value) { return value === null || value === undefined ? "未判分" : Number(value).toFixed(2); }
function itemLabel(row) { return row.scoring_state && row.scoring_state !== "scored" ? resultStates[row.scoring_state] : statuses[row.status]; }
function usageValue(usage) {
  if (usage.responses_without_usage && !usage.responses_with_usage) return "未上报";
  return (usage.total_tokens || 0).toLocaleString() + (usage.responses_without_usage ? "（部分）" : "");
}
function showResult(result) {
  currentResult = result;
  const summary = result.summary;
  $("empty-results").hidden = true; $("result-content").hidden = false;
  $("result-status").textContent = resultStates[summary.result_state] || states[summary.status] || summary.status;
  $("result-model").textContent = summary.model_identity?.name || summary.model;
  $("result-date").textContent = formatDate(summary.scored_at);
  const legacy = !(summary.protocol || "").includes("1.1");
  const protocol = legacy ? "历史协议 1.0" : "协议 1.1 · 总结专榜 / 作品名辅助识图";
  const datasetName = {"dev-v2.0":"广域开发集 400/400/100","dev-v1.2":"旧开发集 50/50/20","v2.0":"流程样题 5/3/2","v1.1":"旧流程样题 5/3/2","v1":"历史样题"}[summary.dataset_id] || "历史或自定义题库";
  $("result-banner").textContent = (summary.mode === "mock_demo" ? "DEMO · 不进入排行和战绩馆" : summary.mode === "agent_cli" ? "AGENT 赛区 · 本地开发赛" : "Agent 成绩") + " · " + datasetName;
  if(summary.mode!=="mock_demo")$("result-banner").textContent += " · "+(identityNames[summary.model_identity?.source]||"模型来源未记录");
  renderResultOverview(result);
  $("score-cards").replaceChildren();
  for (const board of ["text","vision","story"]) {
    const value = summary.boards[board];
    $("score-cards").append(makeRouteResult(board,value,summary,legacy));
  }
  const ops = summary.operations;
  const judgingFailed = result.scores.filter(r=>r.scoring_state === "judge_failed").length;
  const reviewPending = result.scores.filter(r=>r.scoring_state === "needs_review").length;
  $("operation-stats").replaceChildren(...[
    `答题调用 ${ops.completed_target_calls}/${ops.planned_target_calls}`,
    `原调用异常 ${ops.execution_failed_samples || 0}`,
    `中途告警已恢复 ${ops.recovered_target_warning_calls || 0}`,
    `图片声明提醒 ${ops.image_capability_advisory_calls || 0}（不计调用错误）`,
    `答题并发 ${ops.initial_target_concurrency ?? "未记录"} → ${ops.final_target_concurrency ?? "未记录"}`,
    `裁判调用 ${ops.judge_calls_this_grading} · 缓存 ${ops.judge_cache_hits}`,
    `返回 token：答题 ${usageValue(ops.generation_usage)} / 裁判 ${usageValue(ops.judge_usage_this_grading)}`,
    `思考 token：答题 ${ops.generation_usage.reasoning_tokens || 0} / 裁判 ${ops.judge_usage_this_grading.reasoning_tokens || 0}`,
    `输出截断：答题 ${ops.truncated_target_calls || 0} / 裁判 ${ops.truncated_judge_calls || 0}`,
    `判分失败 ${judgingFailed} · 待复核 ${reviewPending}`,
    `裁判 HTTP 重试 ${ops.judge_http_retries || 0} · 网络补判 ${ops.judge_retry_samples || 0} · 恢复 ${ops.judge_retry_recovered_samples || 0}`,
    `未发出请求 ${ops.skipped_target_calls || 0}`,
    `人工补分 ${ops.manual_scored_samples || 0} · 待补 ${ops.manual_score_pending_samples ?? result.scores.filter(r=>r.item_score == null).length}`,
    `规则评分 ${ops.rules_scored_samples ?? "未记录"} 个样本`,
    `答题阶段 ${duration(ops.generation_wall_seconds)}${ops.generation_wall_estimated ? "（历史估算）" : ""}`,
    `每次回答：平均 ${duration(ops.answer_timing?.mean_seconds)} / 中位 ${duration(ops.answer_timing?.median_seconds)} / P95 ${duration(ops.answer_timing?.p95_seconds)}`,
    `判分阶段 ${duration(ops.grading_wall_seconds)} · ${ops.grading_concurrency || "未记录"} 并发`,
    `累计报错 ${ops.runtime_issues?.total??"未记录"} · 答题失败 ${ops.runtime_issues?.target??"—"} · 裁判异常 ${ops.runtime_issues?.judge??"—"} · 重试告警 ${ops.runtime_issues?.warnings??"—"}`,
    `用户停止而取消的答题 ${ops.cancelled_target_calls||0} 次（不计入报错）`
  ].map(text=>node("span","",text)));
  const explanations = [];
  if(summary.stopped)explanations.push("本轮由用户停止。已完成记录和可用分数保留，未完成题目不按知识错误计零分；该次记录不参与完整自动成绩排名。");
  if (summary.execution_abort) explanations.push(`配置错误，已停止后续请求：${summary.execution_abort.reason}`);
  if (summary.mode === "agent_cli") explanations.push("Harness 沿用各自提示和生成配置；可配置工具会关闭并审计调用轨迹。工作目录不是完整系统隔离；成绩记录具体客户端及配置。");
  if (ops.generation_usage.responses_without_usage || ops.judge_usage_this_grading.responses_without_usage) explanations.push("部分客户端未返回 token 用量，显示值只包含已上报部分，不能当作总用量或零消耗。");
  if (summary.source_score_scale === 1000) explanations.push("历史成绩已除以 10 换算为百分制展示；下载的原始文件保留当时的千分制单位。");
  if (ops.truncated_target_calls) explanations.push(`有 ${ops.truncated_target_calls} 次答题输出达到上限。思考过程与最终答案共用预算，请提高输出上限后重新测试；新版将其标为执行问题，不按知识错误计零分。`);
  if (ops.truncated_judge_calls) explanations.push(`有 ${ops.truncated_judge_calls} 次裁判输出达到上限，相关题目待复核。请提高裁判输出上限，或关闭裁判思考模式。`);
  if (legacy) explanations.push("旧存档保留原协议与分数，不能直接与新版总结榜或给定作品名的识图榜比较。");
  if (result.dataset_matches === false) explanations.push("此存档的题库版本与当前不同，题面与参考答案不再展示；原始回答和历史分数仍可查看。");
  if (summary.status !== "complete") explanations.push("显示已评分部分的参考分，同时列出覆盖率和总分可能范围；异常题未按答错计零分，不能与完整成绩直接比较。请前往‘异常与补分’处理未评分题目。");
  if (ops.manual_scored_samples) explanations.push("此成绩包含人工补分，原始执行记录保持不变；人工总分未拆分为总结四维分数，不进入正式榜单。");
  if (summary.concurrency_adjustments?.length) explanations.push(`CLI 出现重连或限流，后续并发已逐步降至 ${summary.concurrency_adjustments.at(-1).limit}；最终完成的回答仍正常计分。`);
  if (judgingFailed) explanations.push("裁判调用或评分输出异常。检查裁判客户端后可‘仅补判失败题’，无需重新答题；离线重评无法补回缺失的裁判响应。");
  if (reviewPending) explanations.push("存在正常回答需要语义复核：可能未配置裁判，或裁判明确要求人工确认。可补充裁判或人工标注后重新评分。");
  if (Object.values(summary.boards).some(b=>b.missing_categories.length)) explanations.push("文本榜缺少必需类别，仅提供已有题目的分项成绩。");
  $("result-warning").hidden = !explanations.length;
  $("result-warning").textContent = explanations.join(" ");
  $("download-links").replaceChildren();
  const names = {"report.md":"报告 MD","scores.csv":"成绩 CSV","summary.json":"统计 JSON","predictions.jsonl":"原始回答","review_requests.jsonl":"复核清单","judge_errors.jsonl":"裁判错误","manual_scores.jsonl":"人工评分记录","runtime_events.jsonl":"运行异常记录"};
  for (const [file,title] of Object.entries(names)) {
    if (!result.files.includes(file)) continue;
    const link = node("a","",title); link.href = `/api/files/${encodeURIComponent(summary.run_id)}/${file}`;
    link.download = file; $("download-links").append(link);
  }
  $("output-path").textContent = `保存位置：${result.output}`;
  if (summary.source_run) $("output-path").textContent += ` · 原始存档：${summary.source_run}`;
  $("view-mistakes").textContent = `查看错题（${result.scores.filter(isMistake).length}）`;
  $("retry-judge-button").hidden = !judgingFailed || !result.judge;
  $("retry-judge-hint").hidden = $("retry-judge-button").hidden;
  $("mistakes-context").textContent = `${summary.model} · ${formatDate(summary.scored_at)} · ${summary.run_id}`;
  $("issues-context").textContent = $("mistakes-context").textContent;
  $("view-issues").textContent = `异常与人工补分（待补 ${result.scores.filter(r=>r.item_score == null).length}）`;
  $("result-filter").value = "all";
  renderQuestions(); renderIssues();observeNovelContent();
}
function isMistake(row) { return row.item_score == null || row.item_score < 1-1e-9; }
function renderQuestions() {
  if (!currentResult) return;
  const filter = $("result-filter").value;
  $("question-results").replaceChildren();
  const rows = currentResult.scores.filter(row=>isMistake(row) && (filter === "all" || row.board === filter));
  $("mistakes-count").textContent = `未满分 ${rows.length} / ${currentResult.scores.filter(row=>filter === "all" || row.board === filter).length} 个样本`;
  if (!rows.length) $("question-results").append(node("p","empty-state","当前筛选下没有未满分题目。"));
  for (const row of rows) $("question-results").append(questionDetail(row));
}
function hasIssue(row) { return row.item_score == null || row.has_execution_issue || row.has_manual_score || Object.values(row.responses || {}).some(r=>r.warnings?.length); }
function renderIssues() {
  if (!currentResult) return;
  const filter = $("issues-filter").value;
  const rows = currentResult.scores.filter(row=>hasIssue(row) && (filter === "all" || (filter === "manual" ? row.has_manual_score : row.item_score == null)));
  $("issues-count").textContent = `${rows.length} 个样本 · 待补 ${currentResult.scores.filter(r=>r.item_score == null).length} · 已人工 ${currentResult.scores.filter(r=>r.has_manual_score).length}`;
  $("issue-results").replaceChildren(...rows.map(questionDetail));
  if (!rows.length) $("issue-results").append(node("p","empty-state","当前筛选下没有需要处理的异常。"));
}
function questionDetail(row) {
    return makeQuestionCard({id:row.question_id,board:row.board,work_title:row.work_title || row.work,input:row.question,images:row.images,
      badge:itemLabel(row),status:row.status,score:row.item_score,repeat:row.repeat,manual:row.has_manual_score},()=>openResultQuestion(row));
}
function resultQuestionBody(row) {
    const body = node("div","question-body");
    body.append(node("p","muted small",`回答用时 ${duration(row.answer_elapsed_seconds)} · ${row.grading_route === "rules" ? "固定答案规则评分" : "语义评分 / 参考答案精确匹配"}`));
    body.append(node("h4","","题目"));
    const question = row.question;
    if (question && row.board === "story") {
      body.append(node("pre","",question.excerpt.turns.map(t=>`${t.speaker}：${t.text}`).join("\n")));
      body.append(node("p","question-instruction",question.prompt || `A：${question.stage_a.prompt}\nB：${question.stage_b.prompt}`));
    } else if (question) {
      body.append(node("p","question-instruction",question.prompt));
      if (question.scope) body.append(node("p","muted small",Object.values(question.scope).filter(v=>typeof v === "string").join(" · ")));
      if (question.work_title) body.append(node("p","question-instruction",`给定作品：${question.work_title}`));
    } else body.append(node("p","muted small","原题不在当前题库中"));
    for (const source of row.images || []) {
      const img = node("img","question-portrait"); img.src = source;
      img.alt = "本题使用的角色立绘"; img.loading = "lazy"; body.append(img);
    }
    if (row.reference) { body.append(node("h4","","参考答案"),node("pre","",row.reference)); }
    for (const [stage, value] of Object.entries(row.stages)) {
      const response = row.responses[stage] || {};
      const title = stage === "answer" ? "模型回答" : stage === "a" ? (row.stages.b ? "A · 剧情总结（旧版）" : "剧情总结") : "B · 作品识别与后续推测（旧版）";
      const executionFailed = response.execution_state === "error" || value.status === "ERROR";
      const targetStopped=response.error==="run_stopped_by_user" || (executionFailed&&value.reason==="run_stopped_by_user");
      if(targetStopped) {
        body.append(node("h4","",response.executed===false?"用户停止 · 未发起调用":"用户停止 · 回答未完成"),node("p","notice","本题由测试者停止，不计入报错次数，不按知识错误计零分。"));
        if(response.text)body.append(node("h4","","停止前保留的输出（尚未评分）"),node("pre","",response.text));
      } else if (executionFailed) {
        body.append(node("h4","",response.executed === false ? "未发起调用" : value.manual_score ? "原执行错误（已人工补分）" : "执行错误（未判分）"));
        body.append(node("pre","execution-error",response.error || value.reason || "调用未完成"));
        if (response.text) body.append(node("h4","","未完成的输出（不参与评分）"),node("pre","",response.text));
      } else {
        body.append(node("h4","",title),node("pre","",response.text || "未生成回答"));
        if (row.scoring_state === "judge_failed") body.append(node("p","notice error","判分失败：保留以上模型回答，可修复裁判后重新评分。"));
        if(value.reason==="run_stopped_by_user")body.append(node("p","notice","评分由用户停止；以上完整回答已保留，可继续人工评分。"));
      }
      if (response.finish_reason === "length") body.append(node("p","notice warning","Harness 报告输出被截断，请在 Agent 客户端提高生成预算后重测。"));
      if (response.warnings?.length) body.append(node("p","notice warning",`调用中途出现告警，最终已完成：${response.warnings.join("；")}`));
      if (value.original_assessment) body.append(node("h4","","原始异常记录"),node("pre","execution-error",value.original_assessment.reason || value.original_assessment.status));
      if (value.manual_score) {
        body.append(node("p","notice",`人工得分 ${value.manual_score.score_percent} / 100 · ${value.manual_score.reason} · ${formatDate(value.manual_score.recorded_at)}`));
        if (value.manual_score.replacement_answer) body.append(node("h4","","人工补录回答（原始输出保留）"),node("pre","",value.manual_score.replacement_answer));
      }
      if (row.manual_contexts?.[stage]) body.append(manualForm(row,stage,value));
      const usage = response.usage || {};
      body.append(node("p","muted small",`本次回答用时 ${duration(response.elapsed_seconds)}`));
      if (usage.completion_tokens !== undefined) body.append(node("p","muted small",`输出 ${usage.completion_tokens} tokens，其中思考 ${usage.completion_tokens_details?.reasoning_tokens || 0} tokens。`));
      const reasons = {output_limit_reached:"答题输出预算耗尽",judge_output_limit_reached:"裁判输出预算耗尽",judge_refused:"裁判拒绝评分",semantic_judge_or_manual_review_required:"需要模型裁判或人工复核",generation_not_completed:"答题未完成",accepted_entity:"命中已核验的人物名称",not_in_verified_aliases:"未命中已核验的人物名称",abstention_with_answer:"已声明放弃，却同时提交了非空答案，按无效回答计分"};
      const stageLabel = value.reason==="run_stopped_by_user"?"用户停止":value.status === "HOLD" && row.scoring_state === "judge_failed" ? "判分失败" : statuses[value.status] || value.status;
      body.append(node("p","reason",`评分：${stageLabel} · ${value.points === null ? "未判分" : value.points+" / "+value.max_points} · ${value.method || "规则"}${value.reason ? " · "+(reasons[value.reason] || value.reason) : ""}`));
      if (value.summary_length_chars !== undefined) {
        const range = question?.summary_length || question?.stage_a?.summary_length;
        body.append(node("p",value.summary_length_status === "within_range" ? "muted small" : "notice warning",
          `总结正文 ${value.summary_length_chars} 字${range ? `，题目要求 ${range.min}–${range.max} 字` : ""}。原文完整保留${value.summary_caps?.includes("over_twice_max_length") ? "；超过上限两倍，本题得分封顶为满分的一半" : ""}。`));
      }
    }
    if (Object.keys(row.dimension_scores || {}).length) {
      body.append(node("h4","","分项得分"));
      const dimensions = {coverage:"事件覆盖",relations:"关系与因果",faithfulness:"忠实度",expression:"表达",work_identification:"作品识别",character_identification:"人物识别",prediction_grounding:"推测依据",prediction_match:"后续匹配"};
      const list = node("div","dimension-list");
      for (const [key,value] of Object.entries(row.dimension_scores)) list.append(node("span","",`${dimensions[key] || key}：${Number(value).toFixed(2)}`));
      body.append(list);
    }
    body.append(node("p","reason",`输出格式：${row.has_execution_issue ? "未评估（原答题调用未完成）" : row.format_compliant ? "合规" : "未完全合规（内容分与格式分分开统计）"}`));
    return body;
}
function manualForm(row,stage,value) {
  const form = node("form","manual-form");
  form.append(node("h4","",value.manual_score ? "修订人工评分" : "人工补分"));
  const scoreLabel = node("label","","人工得分 / 100"), input = node("input");
  input.type="number"; input.min="0"; input.max="100"; input.step="any"; input.required=true; input.name="score_percent";
  if (value.manual_score) input.value=value.manual_score.score_percent;
  scoreLabel.append(input);
  const reasonLabel = node("label","","评分依据（必填）"), reason = node("textarea"); reason.required=true; reason.maxLength=2000; reason.rows=3; reason.name="reason";
  reason.value=value.manual_score?.reason || ""; reasonLabel.append(reason);
  const answerLabel = node("label","","补录已核验的回答（选填，原始回答不变）"), answer = node("textarea"); answer.rows=3; answer.maxLength=20000; answer.name="replacement_answer";
  answer.value=value.manual_score?.replacement_answer || ""; answerLabel.append(answer);
  const button = node("button","secondary","保存人工评分并更新成绩"); button.type="submit";
  const feedback=node("p","notice error");feedback.hidden=true;feedback.setAttribute("role","alert");
  form.append(scoreLabel,reasonLabel,answerLabel,button,feedback);
  form.addEventListener("submit",async event=>{
    event.preventDefault(); if (busy || !form.reportValidity()) return;
    const payload={run_id:currentResult.summary.run_id,question_id:row.question_id,repeat:row.repeat,stage,context:row.manual_contexts[stage],
      score_percent:Number(input.value),reason:reason.value.trim(),replacement_answer:answer.value};
    error(""); setBusy(true); button.disabled=true; $("manual-save-status").textContent="正在保存人工评分，不调用模型…";
    try {
      const result=await api("/api/manual-score",{method:"POST",headers:{"Content-Type":"application/json","X-Galbench-Token":bootstrap.token},body:JSON.stringify(payload)});
      closeDialog("question-dialog");showResult(result); setView("issues"); await loadHistory(); $("manual-save-status").textContent=`已保存 ${row.question_id} · 成绩已更新，原始记录保留。`;showToast("人工评分已保存 ♡");
    } catch (exc) { feedback.hidden=false;feedback.textContent=exc.message; $("manual-save-status").textContent="未保存，请检查评分内容或重新打开存档。"; }
    finally { setBusy(false); button.disabled=false; }
  });
  return form;
}
async function loadHistory() {
  await loadArena();
}
function setView(name) {
  const previousView=document.body.dataset.view;
  for (const view of ["dashboard","leaderboard","mistakes","issues","bank"]) $(view+"-view").hidden = view !== name;
  document.body.dataset.view=name;
  const active=["mistakes","issues"].includes(name)?"dashboard":name;
  for (const button of document.querySelectorAll(".page-nav button[data-view]")) {
    button.classList.toggle("selected", button.dataset.view === active);
    if (button.dataset.view === active) button.setAttribute("aria-current","page"); else button.removeAttribute("aria-current");
  }
  renderHeroTitle(name);
  if(previousView!==name)revealView($(name+"-view"));
  if(previousView&&previousView!==name)window.scrollTo({top:0,behavior:"auto"});
  if (name === "bank" && !bankLoaded) loadBank();
}
async function loadBank() {
  const seq = ++bankSequence;
  $("bank-count").textContent = "正在读取题库…";
  const query = new URLSearchParams({profile:$("bank-profile").value,board:$("bank-board").value,q:$("bank-search").value,page:String(bankPage),limit:"20"});
  if(novelUI.bankWork)query.set("work",novelUI.bankWork);
  for(const key of ["developer","year"])if($("bank-"+key).value!=="all")query.set(key,$("bank-"+key).value);
  try {
    const result = await api(`/api/questions?${query}`);
    if (seq !== bankSequence) return;
    bankLoaded = true; bankPage = result.page; bankPages = result.pages;
    renderBankShelf(result.works,result.collection_total);
    for(const [key,values,label] of [["developer",result.coverage?.developers||[],"全部厂商"],["year",result.coverage?.years||[],"全部年份"]]){
      const select=$("bank-"+key),previous=select.value;
      select.replaceChildren(new Option(label,"all"),...values.map(value=>new Option(key==="developer"?developerNames[value]||String(value):String(value),String(value))));
      select.value=values.some(value=>String(value)===previous)?previous:"all";
    }
    $("bank-count").textContent = `共 ${result.total} 道题 · 每页最多 20 道`;
    $("bank-page").textContent = `第 ${bankPage} / ${bankPages} 页`;
    $("bank-prev").disabled = bankPage <= 1; $("bank-next").disabled = bankPage >= bankPages;
    $("bank-questions").replaceChildren();
    for (const q of result.items) {
      const card=makeQuestionCard(q,()=>openBankQuestion(q));card.classList.add("bank-question");$("bank-questions").append(card);
    }
    if (!result.items.length) $("bank-questions").append(node("p","empty-state","没有匹配的题目。"));
    observeNovelContent();
  } catch (exc) { if (seq === bankSequence) { $("bank-count").textContent = "题库读取失败"; error(exc.message); } }
}
async function regradeCurrent(useJudge = false) {
  if (!currentResult || busy) return;
  const payload = {run_id:currentResult.summary.run_id,use_judge:useJudge};
  if (useJudge) {
    setMode("agent_cli");
    if (!$("judge-enabled").checked) {openConfig("judge");return error("请在选手准备室启用模型裁判。");}
    payload.judge = config("judge");

  }
  error(""); setBusy(true); $(useJudge ? "retry-judge-button" : "rescore-button").textContent = useJudge ? "正在低并发补判…" : "正在复用本地评分记录…";
  try {
    const result = await api("/api/rescore",{method:"POST",headers:{"Content-Type":"application/json","X-Galbench-Token":bootstrap.token},body:JSON.stringify(payload)});
    showResult(result); await loadHistory();
  } catch (exc) { error(exc.message); }
  finally { setBusy(false); $("rescore-button").textContent = "按现有记录重新评分（不调用模型）"; $("retry-judge-button").textContent = "仅补判失败题"; }
}
async function init() {
  try {
    initArenaUI();
    initQuestUI();
    initNovelUI();
    bootstrap = await api("/api/bootstrap");
    for (const model of bootstrap.codex_models || []) {
      const option = node("option"); option.value = model.id; option.label = model.label; $("codex-model-ids").append(option);
    }
    bootstrap.datasets ||= [{id:"smoke",label:"流程样题 · 5 / 3 / 2",boards:bootstrap.boards}];
    for (const dataset of bootstrap.datasets) {
      const option = node("option","",dataset.label); option.value = dataset.id; $("dataset-profile").append(option);
      const bankOption = node("option","",dataset.label); bankOption.value = dataset.id; $("bank-profile").append(bankOption);
    }
    $("dataset-profile").value = bootstrap.default_dataset || bootstrap.datasets[0].id;
    $("bank-profile").value = $("dataset-profile").value;
    for(const agent of bootstrap.agents)$("rank-mode").append(new Option(agent.label,agent.id));
    for (const role of ["target","judge"]) {
      $(role+"-provider").replaceChildren(...bootstrap.agents.map(agent=>{const option=node("option","",agent.label+" · Harness");option.value=agent.id;return option;}));
      $(role+"-provider").addEventListener("change",()=>{
        rememberExecutable(role);rememberModel(role);$(role+"-cli-status").textContent = "";
        $(role+"-executable").value = "";fillExecutable(role);fillModel(role);
        if (role === "target" && isAgent(role)) $("target-vision").checked = false;
        updateControls(); saveSettings();
      });
      $(role+"-check-cli").addEventListener("click",()=>checkCli(role));
      $(role+"-model-template").addEventListener("click",()=>applyDeepSeekTemplate(role));
      $(role+"-detect-cli").addEventListener("click",()=>detectCli(role));
      $(role+"-executable").addEventListener("input",()=>{const input=$(role+"-executable");input.dataset.pathSource=input.value.trim()?"manual":"auto";$(role+"-cli-status").textContent="";rememberExecutable(role);$(role+"-path-source").textContent=input.value.trim()?"手工指定 · 自动识别不会覆盖":"留空自动查找";});
      $(role+"-cli-model").addEventListener("input",()=>{rememberModel(role);updateTransport(role);saveSettings();});
      $(role+"-cli-model").addEventListener("change",()=>{normalizeCliModel(role);rememberModel(role);updateTransport(role);saveSettings();});
    }
    $("target-vision").checked=false; // Image capability belongs to the selected Agent model.
    restoreSettings();
    for (const role of ["target","judge"]) {normalizeCliModel(role);fillExecutable(role);fillModel(role);}
    if (!bootstrap.datasets.some(d=>d.id === $("dataset-profile").value)) $("dataset-profile").value = bootstrap.default_dataset || bootstrap.datasets[0].id;
    setMode(mode); saveSettings();
    setView("dashboard");loadShowcase();
    $("mode-agent").addEventListener("click",()=>{setMode("agent_cli");saveSettings();});
    $("mode-demo").addEventListener("click",()=>{setMode("mock_demo");saveSettings();});
    for (const el of [...document.querySelectorAll("#model-form input, #model-form select, #run-fields input, #run-fields select")]) {
      el.addEventListener("change",()=>{updateControls();saveSettings();});
    }
    for (const button of document.querySelectorAll("[data-reveal]")) button.addEventListener("click",()=>{
      const input = $(button.dataset.reveal); input.type = input.type === "password" ? "text" : "password";
      button.textContent = input.type === "password" ? "显示" : "隐藏";
      button.setAttribute("aria-label",`${input.type === "password" ? "显示" : "隐藏"}密钥`);
    });
    $("model-form").addEventListener("submit",(event)=>event.preventDefault());
    $("start-button").addEventListener("click",start);
    $("stop-run").addEventListener("click",stopCurrentRun);
    $("try-demo").addEventListener("click",()=>{setMode("mock_demo");start();});
    $("result-filter").addEventListener("change",renderQuestions);
    $("view-mistakes").addEventListener("click",()=>setView("mistakes"));
    $("view-issues").addEventListener("click",()=>setView("issues"));
    $("issues-filter").addEventListener("change",renderIssues);
    $("rescore-button").addEventListener("click",()=>regradeCurrent(false));
    $("retry-judge-button").addEventListener("click",()=>regradeCurrent(true));
    for (const button of document.querySelectorAll(".page-nav button[data-view]")) button.addEventListener("click",()=>setView(button.dataset.view));
    for (const id of ["bank-profile","bank-board","bank-developer","bank-year"]) $(id).addEventListener("change",()=>{if(id==="bank-profile"){novelUI.bankWork="";$("bank-developer").value="all";$("bank-year").value="all";}bankPage=1;loadBank();});
    $("bank-search").addEventListener("input",()=>{clearTimeout(bankDebounce);bankDebounce=setTimeout(()=>{bankPage=1;loadBank();},300);});
    $("bank-prev").addEventListener("click",()=>{bankPage=Math.max(1,bankPage-1);loadBank();});
    $("bank-next").addEventListener("click",()=>{bankPage=Math.min(bankPages,bankPage+1);loadBank();});
    $("refresh-history").addEventListener("click",loadHistory);
    await loadHistory();
    if (bootstrap.active_run) { enterArena(true);currentRun = bootstrap.active_run; setBusy(true); await poll(); }
  } catch (_) { error("本地服务未能初始化。请确认通过 start_galbench.bat 启动，再刷新网页。"); }
}
init();
