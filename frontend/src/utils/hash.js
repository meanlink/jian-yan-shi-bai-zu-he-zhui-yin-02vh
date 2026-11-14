// 与后端 Python json.dumps(sort_keys=True, ensure_ascii=False) 完全对齐的规范化序列化，
// 保证浏览器计算的 SHA256 与服务端 checksum() 一致。
export function canonicalJSON(value){
 if(value===null)return 'null';
 const t=typeof value;
 if(t==='string')return JSON.stringify(value);
 if(t==='number'){
   if(!Number.isFinite(value))throw new Error('存在非有限数值，无法计算指纹');
   return String(value);
 }
 if(t==='boolean')return value?'true':'false';
 if(Array.isArray(value))return '['+value.map(canonicalJSON).join(',')+']';
 if(t==='object'){
   const keys=Object.keys(value).sort();
   return '{'+keys.map(k=>JSON.stringify(k)+':'+canonicalJSON(value[k])).join(',')+'}';
 }
 throw new Error('不支持的数据类型：'+t);
}
const enc=new TextEncoder();
export async function sha256(value){
 const digest=await crypto.subtle.digest('SHA-256',enc.encode(canonicalJSON(value)));
 return [...new Uint8Array(digest)].map(b=>b.toString(16).padStart(2,'0')).join('');
}
export function uuid(){
 if(crypto.randomUUID)return crypto.randomUUID();
 return 'xxxxxxxxxxxx4xxxyxxxxxxxxxxxxxxx'.replace(/[xy]/g,c=>{const r=Math.random()*16|0;return (c==='x'?r:(r&0x3|0x8)).toString(16);});
}
// 按目标字节数贪心切分记录，保证每片至少一条；片数上限由后端 total_chunks<=2000 兜底。
export function splitRecords(records,targetBytes=512*1024){
 const chunks=[];let current=[];let size=2;
 for(const record of records){
   const recordBytes=enc.encode(canonicalJSON(record)).length+1;
   if(current.length&&size+recordBytes>targetBytes){chunks.push(current);current=[];size=2;}
   current.push(record);size+=recordBytes;
 }
 if(current.length)chunks.push(current);
 return chunks;
}
