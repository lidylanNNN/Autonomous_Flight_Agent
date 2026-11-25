const $ = id => document.getElementById(id);
let selected = '', data = null, session = {}, polling = false;
const originalTask = '补拍桥塔一处高处横撑：交付能定位构件的全景、能看清指定表面的近景，以及对应的照片记录，然后返航。';
const names = {takeoff:'起飞', goto:'飞往目标', capture_photo:'拍摄新照片', inspect_image:'图像检测', rtl:'返航', land:'降落', hold:'悬停'};
const mediaUrl = path => '/media?path=' + encodeURIComponent(path);
const element = (tag, cls, text) => { const node = document.createElement(tag); node.className = cls || ''; if (text !== undefined) node.textContent = text; return node; };
async function api(path, body) {
  const options = body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-Flight-UI':'1'},body:JSON.stringify(body)};
  const response = await fetch(path, options), result = await response.json();
  if (!response.ok) throw new Error(result.error || '请求失败'); return result;
}
function notify(text) { $('toast').textContent=text; $('toast').classList.remove('hidden'); setTimeout(()=>$('toast').classList.add('hidden'),4200); }
function title(id) { if(id===session.run_id) return '当前实时会话'; if(id.includes('bridge_original')) return '桥梁 · 原指令实测'; if(id.includes('bridge_capture')) return '桥梁 · 详细指令演示'; if(id.includes('m7_flight_demo')) return '仓库 · 单点拍照'; return id.replace(/_/g,' '); }
function liveSelected() { return selected && selected===session.run_id; }
function connection() {
  $('connection').textContent=session.active ? (session.ready?'真实入口等待输入':'真实入口运行中') : '未连接实时入口';
  $('connect').disabled=!session.enabled || session.active;
  $('connect').textContent=session.active?'入口已连接':'连接真实入口';
  $('modeBadge').textContent=liveSelected()?'实时会话':'记录回放'; $('modeBadge').className='badge'+(liveSelected()?' live':'');
  $('send').textContent=liveSelected()?'发送并执行':'保存草稿';
  $('send').disabled=liveSelected()&&!session.ready;
  $('sendHint').textContent=liveSelected()?'提交后将由现有 Agent 立即执行；输入不改写。当前未接入规划确认或网页急停。':'当前为历史回放。保存草稿不会调用模型或触发飞行。';
}
async function listRuns() {
  const runs=await api('/api/runs'); $('runCount').textContent=runs.length; $('runs').replaceChildren();
  for(const run of runs){ const b=element('button','run'+(run.id===selected?' current':'')); b.append(element('strong','',title(run.id)),element('small','',run.id)); b.onclick=()=>select(run.id); $('runs').append(b); }
  return runs;
}
async function select(id) { selected=id; await load(); await listRuns(); connection(); }
async function load() {
  if(!selected)return; data=await api('/api/run?id='+encodeURIComponent(selected));
  $('runTitle').textContent=title(selected); $('runSubtitle').textContent=liveSelected()?'任务原文接入既有 ros_agent_srv；状态时效与动作结果独立显示。':selected;
  $('human').textContent=data.human||'尚未提交任务。'; $('inputKind').textContent='本轮实际提交';
  const correction=data.correction?.original_instruction_was_submitted===false;
  $('scope').classList.toggle('hidden',!correction); $('scope').textContent='本轮输入包含人工展开的坐标与动作顺序，只验证执行链，未验证原任务的自主规划。';
  $('rawResponse').textContent=data.llm?.response||'尚无模型响应'; $('console').textContent=data.console||'这份历史记录没有 UI 入口终端日志；请查看模型响应与 Trace。';
  const goal=data.result?.task_success, behavior=data.report?.success;
  $('verdict').textContent=goal===false?'任务未完成':behavior===false?'执行失败':behavior===true?'执行已结束':'等待执行记录';
  $('verdict').className='badge'+(goal===false||behavior===false?' fail':behavior===true?' success':'');
  const video=$('video'), current=data.video?mediaUrl(data.video):'';
  video.poster=data.poster?mediaUrl(data.poster):'';
  if(video.dataset.path!==current){video.dataset.path=current;if(current){video.src=current;video.load();}else{video.removeAttribute('src');video.load();}}
  const latestPhoto=liveSelected()?data.photos.at(-1):null;
  if(latestPhoto)$('livePhoto').src=mediaUrl(latestPhoto.path);
  $('livePhoto').classList.toggle('hidden',!latestPhoto);
  video.classList.toggle('hidden',!current); $('emptyVideo').classList.toggle('hidden',!!current||!!latestPhoto);
  $('viewerLabel').textContent=current?'GAZEBO · 本轮历史录像':latestPhoto?'本轮最近拍照结果 · 非直播':'实时状态 · 相机直播未接入';
  const events=[...data.safety,...(data.agent_events||[])].sort((a,b)=>Date.parse(a.recorded_at)-Date.parse(b.recorded_at));
  $('trace').replaceChildren(); $('traceCount').textContent=events.length;
  for(const record of events){ const reject=record.decision?.decision==='REJECT'; const recovery=(data.operator_recovery?.proposal_id&&(record.proposal?.proposal_id===data.operator_recovery.proposal_id||record.command?.proposal_id===data.operator_recovery.proposal_id))||(data.operator_recovery?.execution_id===record.result?.execution_id&&!!record.result?.execution_id);
    const row=element('details','trace-event'+(reject?' rejected':'')+(recovery?' recovery':'')),summary=element('summary');
    const action=record.proposal?.skill_name||record.command?.skill_name||'';
    summary.append(element('time','',new Date(record.recorded_at).toLocaleTimeString('zh-CN',{hour12:false})),element('span','event-tag',recovery?'独立恢复':record.decision?.decision||record.result?.status||record.type),element('span','',action+' '+(record.decision?.reason_codes||[]).join(', ')));
    row.append(summary,element('pre','',JSON.stringify(record,null,2))); $('trace').append(row);
  }
  if(!events.length)$('trace').append(element('p','empty-note','尚无观察、模型或飞行动作记录。'));
  $('media').replaceChildren(); $('photoCount').textContent=data.photos.length;
  for(const photo of data.photos){const a=element('a','media-card');a.href=mediaUrl(photo.path);a.target='_blank';const img=element('img');img.src=a.href;img.alt='本轮机载照片';a.append(img,element('p','',photo.id));$('media').append(a);}
  if(!data.photos.length)$('media').append(element('p','empty-note','本轮尚未交付照片。'));
  $('report').textContent=goal===false?'原任务未完成。动作服务成功不等于任务成功；人工恢复与 Agent 任务分别记录。':behavior===true?'动作执行报告已返回。请结合目标照片与独立状态证据判断任务是否完成。':'模型结果、执行记录和状态将随本轮更新。';
  renderTime();
}
function renderTime(){
  if(!data)return;
  const video=$('video'), cutoff=liveSelected()||!data.video_origin?Infinity:(data.video_origin+video.currentTime)*1000;
  const states=data.states.filter(r=>Date.parse(r.recorded_at)<=cutoff); const sample=liveSelected()?data.last_state:states.at(-1); const state=sample?.state;
  $('flightMode').textContent=state?.flight_mode||'—'; $('altitude').textContent=state?.position_ned_m?(-state.position_ned_m.z).toFixed(2):'—';
  $('battery').textContent=state?.battery_percent!=null?state.battery_percent.toFixed(0)+'%':'—'; $('armed').textContent=state?state.armed?'已解锁':'已上锁':'—'; $('landed').textContent=state?state.landed?'已落地':'空中':'—';
  const age=sample?(Date.now()-Date.parse(sample.recorded_at))/1000:Infinity;
  $('stateNote').textContent=liveSelected()?(age<2?'实测状态持续更新 · '+new Date(sample.recorded_at).toLocaleTimeString():'状态未就绪或已过期；请检查仿真和 ROS 服务。'):'历史状态随录像时间近似对齐，不能作为当前飞行状态。';
  $('actions').replaceChildren();
  data.actions.forEach((action,i)=>{const step=data.report?.steps?.[i];let status='等待执行',kind='';
    let output={};try{output=JSON.parse(step?.output||'{}');}catch(e){}
    const result=output.execution_id?data.safety.find(r=>r.result?.execution_id===output.execution_id):null;
    const decision=output.decision_id?data.safety.find(r=>r.decision?.decision_id===output.decision_id):null;
    const at=Date.parse((result||decision)?.recorded_at||'');
    if(step&&(!Number.isFinite(at)||at<=cutoff)){status=step.success?'最终成功':'最终失败';kind=step.success?' done':' failed';}
    else if(decision&&Date.parse(decision.recorded_at)<=cutoff){status='执行中';}
    else if(data.report?.success===false&&i>=data.report.steps.length){status='未执行';}
    const row=element('div','action'+kind),body=element('div','action-body'),heading=element('div','action-title');
    heading.append(element('span','',names[action.name]||action.name),element('em','',status)); body.append(heading,element('pre','',action.name+' '+JSON.stringify(action.arguments)));
    row.append(element('span','action-index',i+1),body);$('actions').append(row);
  });
  if(!data.actions.length)$('actions').append(element('p','empty-note','等待真实模型输出。'));
}
$('video').addEventListener('timeupdate',renderTime);
$('connect').onclick=async()=>{try{session=await api('/api/connect',{});await select(session.run_id);notify('已启动现有真实入口，等待观察服务与输入就绪。');}catch(e){notify(e.message);}};
$('liveMode').onclick=async()=>{if(session.run_id)await select(session.run_id);else notify('服务器未配置实时环境，请以 --live-dir 启动。');};
$('composer').onsubmit=async event=>{event.preventDefault();const text=$('task').value;if(!text.trim())return;
  if(!liveSelected()){localStorage.setItem('flight_task_draft',text);notify('草稿已保存，未调用模型或执行飞行。');return;}
  try{session=await api('/api/input',{text});connection();notify('原文已发送至真实入口。');await load();}catch(e){notify(e.message);}
};
$('example').onclick=()=>{$('task').value=originalTask;}; $('task').value=localStorage.getItem('flight_task_draft')||'';
document.querySelectorAll('.tab').forEach(button=>button.onclick=()=>{document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('selected',b===button));document.querySelectorAll('.tab-body').forEach(p=>p.classList.toggle('hidden',p.id!==button.dataset.tab));});
async function refresh(){if(polling)return;polling=true;try{session=await api('/api/status');connection();if(liveSelected())await load();}catch(e){$('connection').textContent='本地服务不可达';}finally{polling=false;}}
(async()=>{try{session=await api('/api/status');const runs=await listRuns();const first=runs.find(r=>r.id.includes('bridge_original_input'))||runs[0];if(first)await select(first.id);connection();setInterval(refresh,1400);}catch(e){notify(e.message);}})();
