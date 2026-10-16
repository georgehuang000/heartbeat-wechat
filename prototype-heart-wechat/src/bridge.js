const token=new URLSearchParams(location.hash.slice(1)).get('token');
export const live=Boolean(token);
export const desktop=new URLSearchParams(location.search).get('desktop')==='1';
export async function call(method,payload={}){
 const response=await fetch(`/api/${method}`,{method:'POST',headers:{'Content-Type':'application/json','X-Galchat-Token':token||''},body:JSON.stringify(payload)});
 if(!response.ok)throw new Error(`应用连接失败（${response.status}）`);
 const data=await response.json();
 if(data.ok===false){const error=new Error(data.error||'操作没有完成');error.state=data.state;throw error;}
 return data;
}
