import {api} from './client.js';
export const datasets={list:()=>api('/datasets'),get:id=>api('/datasets/'+id),create:data=>api('/datasets',{method:'POST',body:JSON.stringify(data)})};
export const uploads={
 list:()=>api('/uploads'),
 get:id=>api('/uploads/'+id),
 create:data=>api('/uploads',{method:'POST',body:JSON.stringify(data)}),
 putChunk:(id,index,chunk)=>api('/uploads/'+id+'/chunks/'+index,{method:'PUT',body:JSON.stringify(chunk)}),
 complete:(id,finalChunk=null)=>api('/uploads/'+id+'/complete',{method:'POST',body:JSON.stringify(finalChunk?{final_chunk:finalChunk}:{})}),
 close:id=>api('/uploads/'+id+'/close',{method:'POST',body:'{}'}),
};
export const jobs={list:()=>api('/jobs'),get:id=>api('/jobs/'+id),create:data=>api('/jobs',{method:'POST',body:JSON.stringify(data)}),approve:id=>api('/jobs/'+id+'/approve',{method:'POST'})};
export const reports={list:()=>api('/reports'),create:data=>api('/reports',{method:'POST',body:JSON.stringify(data)}),toggle:id=>api('/reports/'+id+'/toggle',{method:'POST'}),deliveries:()=>api('/deliveries')};
