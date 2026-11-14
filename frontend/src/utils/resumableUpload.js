// 分段续传控制器：服务端会话为状态真源，本地仅缓存当前作业元数据。
// 断线后可凭会话 ID 拉取缺片集合继续上传；同片重试由服务端幂等去重。
import {uploads} from '../api/entities.js';
import {sha256,uuid,splitRecords} from './hash.js';

const PREFIX='resumable-upload:';
const MAX_RETRY=3;
const wait=ms=>new Promise(r=>setTimeout(r,ms));

function cacheKey(token){return PREFIX+token;}
function readCache(token){try{return JSON.parse(localStorage.getItem(cacheKey(token))||'null');}catch{return null;}}
function writeCache(meta){localStorage.setItem(cacheKey(meta.client_token),JSON.stringify(meta));}
export function listCached(){
 return Object.keys(localStorage).filter(k=>k.startsWith(PREFIX))
  .map(k=>{try{return JSON.parse(localStorage.getItem(k));}catch{return null;}}).filter(Boolean);
}
export function clearCache(token){localStorage.removeItem(cacheKey(token));}

// 计算分片描述（不含 records 持久化，避免超出 localStorage 配额）
export async function buildPlan(records){
 const parts=splitRecords(records);
 const chunkChecksums=[];
 for(const part of parts)chunkChecksums.push(await sha256(part));
 return {parts,chunkChecksums,batchChecksum:await sha256(records)};
}

export async function startSession({name,source,records,plan,onProgress}){
 const token=uuid();
 const session=await uploads.create({
  name,source,total_chunks:plan.parts.length,total_records:records.length,
  batch_checksum:plan.batchChecksum,client_token:token,
 });
 const meta={client_token:token,session_id:session.id,name,source,
  total_chunks:plan.parts.length,total_records:records.length,
  batch_checksum:plan.batchChecksum,chunk_checksums:plan.chunkChecksums,
  chunk_sizes:plan.parts.map(p=>p.length),started_at:Date.now()};
 writeCache(meta);
 return runUpload({meta,plan,onProgress});
}

async function putWithRetry(sessionId,index,payload){
 let last;
 for(let attempt=1;attempt<=MAX_RETRY;attempt++){
  try{return await uploads.putChunk(sessionId,index,payload);}
  catch(e){
   last=e;
   // 确定性业务错误（内容冲突、校验失败、体积超限、权限问题），重试无意义
   if(isDeterministic(e))throw e;
   await wait(Math.min(800*attempt,2500));
  }
 }
 throw last;
}

// 完成确认在服务端完全幂等（含 final_chunk 情形），网络超时可安全重试
async function completeWithRetry(sessionId,finalChunk){
 let last;
 for(let attempt=1;attempt<=MAX_RETRY;attempt++){
  try{return await uploads.complete(sessionId,finalChunk);}
  catch(e){
   last=e;
   if(isDeterministic(e))throw e;
   await wait(Math.min(800*attempt,2500));
  }
 }
 throw last;
}

function isDeterministic(e){
 const msg=String(e?.message||'');
 return msg.includes('保留首次接收的原片')||msg.includes('校验和')||msg.includes('超出')
   ||msg.includes('权限')||msg.includes('登录')||msg.includes('不存在')||msg.includes('已失败')||msg.includes('已关闭');
}

// 续传核心：先问服务端缺哪些片，只传缺失片；最后一片与完成确认同时提交。
export async function runUpload({meta,plan,onProgress}){
 let session=await uploads.get(meta.session_id);
 if(session.status==='completed'){onProgress?.(session);clearCache(meta.client_token);return session;}
 if(session.status!=='open')throw new Error('会话不可续传：'+(session.error_message||session.status));
 let missing=new Set(session.missing_indexes);
 const ordered=[...missing].sort((a,b)=>a-b);
 for(let k=0;k<ordered.length;k++){
  const index=ordered[k];
  const payload={chunk_index:index,records:plan.parts[index],checksum:meta.chunk_checksums[index]};
  const isLast=index===meta.total_chunks-1;
  if(isLast){
   session=await completeWithRetry(meta.session_id,payload); // 最后一片随完成确认一起提交
  }else{
   session=await putWithRetry(meta.session_id,index,payload);
  }
  onProgress?.(session);
  if(session.status==='failed')throw new Error(session.error_message||'批次校验失败，批次不可用');
  if(session.status!=='open'&&session.status!=='completed')throw new Error('会话状态异常：'+session.status);
 }
 if(session.status!=='completed')session=await completeWithRetry(meta.session_id); // 纯完成确认（含超时重试幂等）
 onProgress?.(session);
 if(session.status==='completed')clearCache(meta.client_token);
 else throw new Error(session.error_message||'批次未完成，可继续续传');
 return session;
}

// 页面刷新后：重新选择文件，按服务端会话的整批指纹核对后继续缺片续传。
export async function resumeWithFile({meta,records,onProgress}){
 const plan=await buildPlan(records);
 if(plan.batchChecksum!==meta.batch_checksum){
  throw new Error('所选文件与该会话的整批指纹不一致，无法续传；请重新选择原始文件或新建会话');
 }
 if(plan.parts.length!==meta.total_chunks)throw new Error('分片数量与原会话不一致，请确认文件未被修改');
 return runUpload({meta:{...meta,chunk_checksums:plan.chunkChecksums},plan,onProgress});
}
