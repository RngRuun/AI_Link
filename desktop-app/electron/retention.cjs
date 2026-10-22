// Native-only file selection and permanent-delete confirmation.
function createRetention({send,choose,confirm,openPath,openExternal,clearCache,cacheSize}) {
  return {
    async importCloud(label) {
      if(typeof label!=='string'||!label.trim()||label.length>40)throw new Error('请先输入 1–40 字的账户／导出分组名称。');
      const result=await choose({title:'导入 ChatGPT 导出包',properties:['openFile'],filters:[{name:'ChatGPT 导出',extensions:['zip','json']}]});
      if(result.canceled)return null;
      if(result.filePaths.length!==1)throw new Error('请选择一份导出文件。');
      return send('storage.import',{path:result.filePaths[0],label:label.trim()},120000);
    },
    async location(kind) {
      if(!['codex','state','library','cloud'].includes(kind))throw new Error('不支持的数据目录。');
      const result=await send('storage.location',{kind});const error=await openPath(result.path);if(error)throw new Error(error);return true;
    },
    async purge(batchId) {
      if(typeof batchId!=='string'||!/^[a-f0-9]{32}$/.test(batchId))throw new Error('回收批次无效。');
      const plan=await send('storage.purge.preview',{batchId});
      const decision=await confirm({type:'warning',title:'永久清除原始记录',message:`永久清除 ${plan.count} 个原始记录与恢复备份？`,
        detail:plan.warning+'\n\n'+plan.names.slice(0,20).join('\n')+'\n\n保留成果目录：'+plan.preserved,buttons:['取消','永久清除'],defaultId:0,cancelId:0,noLink:true});
      if(decision.response!==1)return null;
      return send('storage.purge',{token:plan.token},120000);
    },
    async cloud(id=null) {
      if(id!==null&&(typeof id!=='string'||!/^cloud:[a-f0-9]{64}$/.test(id)))throw new Error('云端目标无效。');
      const {url}=await send('storage.cloud.target',{id});const parsed=new URL(url);
      if(parsed.origin!=='https://chatgpt.com'||!/^\/(?:c\/[a-zA-Z0-9_-]{1,128})?$/.test(parsed.pathname)||parsed.search||parsed.hash)throw new Error('拒绝不受信任的云端链接。');
      await openExternal(url);return true;
    },
    async cache(clear=false) {
      if(typeof clear!=='boolean')throw new Error('缓存操作无效。');
      const before=await cacheSize();if(clear)await clearCache();return {bytes:await cacheSize(),cleared:clear,before,scope:'AI Link HTTP 资源缓存；保留登录、设置和对话'};
    }
  };
}
module.exports={createRetention};
