function connectionParams(params, credential) {
  if(!params||typeof params!=='object'||Array.isArray(params)||typeof params.shared!=='boolean'||Object.keys(params).some(k=>!['shared','endpoint','tokenRef'].includes(k)))throw new Error('连接参数无效。');
  const result={shared:params.shared};
  if(params.endpoint){
    let url;try{url=new URL(params.endpoint)}catch{throw new Error('请输入本机共享服务地址。')}
    if(!params.shared||url.protocol!=='ws:'||!['127.0.0.1','[::1]'].includes(url.hostname)||!url.port||url.username||url.password||url.pathname!=='/'||url.search||url.hash)throw new Error('共享地址仅允许本机 ws://127.0.0.1:端口。');
    result.endpoint=params.endpoint;
  }
  if(params.tokenRef){
    if(!result.endpoint||!credential||params.tokenRef!==credential.ref)throw new Error('请重新选择共享服务连接凭据。');
    result.tokenFile=credential.path;
  }
  return result;
}
module.exports={connectionParams};
