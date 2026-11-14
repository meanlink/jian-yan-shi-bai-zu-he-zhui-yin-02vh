import {datasets,uploads} from '../api/entities.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {table} from '../components/common/table.js';
import {esc,notify,date} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';
import {parseCSV} from '../utils/csv.js';
import {buildPlan,startSession,resumeWithFile,listCached,clearCache} from '../utils/resumableUpload.js';

const STATUS_TEXT={open:'续传中',completed:'已生成批次',failed:'校验失败不可用',closed:'已关闭'};

export async function dataPage(){
 workspace.datasets=await datasets.list();
 let selected=workspace.datasets[0]?await datasets.get(workspace.datasets[0].id):null,m=workspace.meta;
 const sessions=await uploads.list();
 const readonly=session.user.role==='viewer';
 return heading('02 / DATA INTAKE','数据接入与批次追溯','支持分段续传：断线后自动查缺片续传，同片重试不重复接收；收齐核对总记录数与整批指纹后才开放分析。')
 +'<div class="dataset-grid"><section class="panel"><div class="section-title"><h2>业务批次</h2><span>'+workspace.datasets.length+' 批</span></div><div class="batch-list">'+workspace.datasets.map(d=>'<button class="batch-item" data-dataset="'+d.id+'"><strong>'+esc(d.name)+'</strong><span>'+d.rows+' 条 · '+esc(d.source)+'</span><small>SHA256 '+esc(d.checksum.slice(0,18))+'…</small></button>').join('')+'</div>'
 +'<div class="section-title"><h2>分段续传接入</h2><span>断点自动补片</span></div>'
 +'<form id="resumable-form"><label>批次名称<input name="name" required minlength="2" placeholder="例如：11 月复检记录（分段）"></label><label>上传 CSV 或 JSON（自动分片）<input type="file" id="resumable-file" accept=".json,.csv"></label><p class="hint">每片约 512KB；整批先在浏览器计算 SHA256，收齐后服务端复核总记录数与指纹，未通过不生成批次。</p><button type="submit" class="primary" '+(readonly?'disabled':'')+'>开始分段上传</button></form>'
 +'<div id="transfer-state" class="transfer-state" hidden></div>'
 +'<div class="section-title compact-title"><h2>上传会话</h2><span>可续传 / 查失败原因</span></div><div id="session-list" class="session-list">'+sessionListHtml(sessions)+'</div></section>'
 +'<section class="panel"><div class="section-title"><h2 id="preview-name">'+esc(selected?.name||'尚无批次')+'</h2><span>预览前 30 条</span></div><div id="data-preview">'+table(m.columns,selected?.records.slice(0,30)||[])+'</div>'
 +'<details class="legacy-upload"><summary>整包接入（原有方式，5–5,000 条）</summary><form id="dataset-form" style="margin-top:16px"><label>批次名称<input name="legacy_name" required minlength="2" placeholder="例如：11 月复测批次"></label><label>上传 CSV 或 JSON<input type="file" id="file-input" accept=".json,.csv"></label><label>记录 JSON<textarea name="records" rows="6" placeholder="粘贴 JSON 数组，或先选择上方文件" required></textarea></label><p class="hint">字段：'+m.columns.map(c=>esc(c.key)+' / '+esc(c.label)).join('，')+'。</p><button type="submit" class="primary" '+(readonly?'disabled':'')+'>校验并接入批次</button></form></details></section></div>';
}

function sessionListHtml(sessions){
 if(!sessions.length)return '<p class="hint">暂无上传会话。</p>';
 return sessions.map(s=>{
  const pct=s.total_chunks?Math.round(s.received_chunks/s.total_chunks*100):0;
  return '<article class="session-item status-'+esc(s.status)+'" data-session-id="'+s.id+'"><div class="session-head"><strong>'+esc(s.name)+'</strong><span class="pill '+esc(s.status)+'">'+esc(STATUS_TEXT[s.status]||s.status)+'</span></div>'
   +'<p class="hint">片 '+s.received_chunks+'/'+s.total_chunks+' · 记录 '+s.total_records+' 条 · '+date(s.updated_at)+'</p>'
   +'<div class="progress"><i style="width:'+pct+'%"></i></div>'
   +(s.status==='failed'?'<p class="fail-reason"><b>失败原因：</b>'+esc(s.error_message||'校验未通过')+'（'+esc(s.error_code||'')+'）</p>':'')
   +(s.status==='open'?'<button class="small" data-resume="'+s.id+'">选择原文件续传</button>':'')
   +(s.status==='completed'?'<p class="hint">已生成数据批次 #'+s.dataset_id+'，可前往工作台分析。</p>':'')+'</article>';
 }).join('');
}

async function refreshSessions(){
 const sessions=await uploads.list();
 document.querySelector('#session-list').innerHTML=sessionListHtml(sessions);
 bindSessionButtons();
}

async function refreshBatchList(){
 const list=await datasets.list();
 workspace.datasets=list;
 const container=document.querySelector('.batch-list');
 if(container)container.innerHTML=list.map(d=>'<button class="batch-item" data-dataset="'+d.id+'"><strong>'+esc(d.name)+'</strong><span>'+d.rows+' 条 · '+esc(d.source)+'</span><small>SHA256 '+esc(d.checksum.slice(0,18))+'…</small></button>').join('');
 document.querySelectorAll('.batch-list [data-dataset]').forEach(button=>button.onclick=async()=>{
  try{const d=await datasets.get(button.dataset.dataset);
   document.querySelector('#preview-name').textContent=d.name;
   document.querySelector('#data-preview').innerHTML=table(workspace.meta.columns,d.records.slice(0,30));
  }catch(error){notify(error.message,true);}
 });
}

function renderTransfer(html){
 const el=document.querySelector('#transfer-state');
 el.hidden=false;el.innerHTML=html;
}

function progressHtml(s){
 const pct=s.total_chunks?Math.round(s.received_chunks/s.total_chunks*100):0;
 return '<h3>'+esc(s.name)+'</h3><p class="hint">已收片 '+s.received_chunks+'/'+s.total_chunks+'（'+pct+'%）'
  +(s.missing_indexes&&s.missing_indexes.length?' · 缺片：'+esc(s.missing_indexes.slice(0,12).join(',')+(s.missing_indexes.length>12?'…':'')):' · 全部片已收齐')+'</p>'
  +'<div class="progress"><i style="width:'+pct+'%"></i></div>';
}

export function bindData(refresh){
 bindLegacyForm(refresh);
 bindResumableForm();
 bindSessionButtons();
 document.querySelectorAll('[data-dataset]').forEach(button=>button.onclick=async()=>{
  try{const d=await datasets.get(button.dataset.dataset);
   document.querySelector('#preview-name').textContent=d.name;
   document.querySelector('#data-preview').innerHTML=table(workspace.meta.columns,d.records.slice(0,30));
  }catch(error){notify(error.message,true);}
 });
}

function bindLegacyForm(refresh){
 let source='manual.json';
 const file=document.querySelector('#file-input');
 if(!file)return;
 file.addEventListener('change',async e=>{try{const f=e.target.files[0];if(!f)return;source=f.name;const text=await f.text(),records=f.name.endsWith('.csv')?parseCSV(text):JSON.parse(text);document.querySelector('[name=records]').value=JSON.stringify(records,null,2);}catch(error){notify(error.message,true);}});
 submit(document.querySelector('#dataset-form'),async f=>{
  await datasets.create({name:f.get('legacy_name'),source,records:JSON.parse(f.get('records'))});
  notify('数据已接入，可前往工作台执行分析');await refresh();
 });
}

async function readRecords(input){
 const f=input.files[0];
 if(!f)throw new Error('请先选择 CSV 或 JSON 文件');
 const text=await f.text();
 return {records:f.name.endsWith('.csv')?parseCSV(text):JSON.parse(text),source:f.name};
}

function bindResumableForm(){
 const form=document.querySelector('#resumable-form');
 if(!form)return;
 submit(form,async f=>{
  const name=f.get('name');
  const {records,source}=await readRecords(document.querySelector('#resumable-file'));
  if(!Array.isArray(records)||records.length<5)throw new Error('至少需要 5 条记录');
  renderTransfer('<p class="hint">正在计算整批指纹与分片…</p>');
  const plan=await buildPlan(records);
  renderTransfer('<p class="hint">已切分 '+plan.parts.length+' 片，开始上传…</p>');
  const s=await startSession({name,source,records,plan,onProgress:cur=>renderTransfer(progressHtml(cur))});
  renderTransfer(progressHtml(s)+'<p class="success-reason">批次已收齐并通过总记录数与整批指纹核对，数据批次 #'+s.dataset_id+' 已开放分析。</p>');
  notify('分段上传完成，批次已生成');
  await refreshSessions();
  await refreshBatchList();
 });
}

function bindSessionButtons(){
 document.querySelectorAll('[data-resume]').forEach(btn=>{
  btn.onclick=async()=>{
   const sid=Number(btn.dataset.resume);
   const cached=listCached().find(c=>c.session_id===sid);
   const pick=document.createElement('input');
   pick.type='file';pick.accept='.json,.csv';
   pick.onchange=async()=>{
    try{
     const f=pick.files[0];if(!f)return;
     const text=await f.text();
     const records=f.name.endsWith('.csv')?parseCSV(text):JSON.parse(text);
     const remote=await uploads.get(sid);
     renderTransfer('<p class="hint">正在核对整批指纹并查询缺片…</p>');
     const meta=cached||{client_token:remote.client_token,session_id:sid,name:remote.name,source:remote.source,
      total_chunks:remote.total_chunks,total_records:remote.total_records,batch_checksum:remote.batch_checksum,
      chunk_checksums:[]};
     const s=await resumeWithFile({meta,records,onProgress:cur=>renderTransfer(progressHtml(cur))});
     if(!cached)clearCache(remote.client_token);
     renderTransfer(progressHtml(s)+'<p class="success-reason">续传完成，数据批次 #'+s.dataset_id+' 已开放分析。</p>');
     notify('续传完成');await refreshSessions();await refreshBatchList();
    }catch(e){notify(e.message,true);renderTransfer('<p class="fail-reason"><b>续传失败：</b>'+esc(e.message)+'</p>');}
   };
   pick.click();
  };
 });
}
