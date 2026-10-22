const path = require('node:path');

function validateContext(context) {
  if (!context || typeof context !== 'object' || Array.isArray(context)) throw new Error('导入归属无效。');
  const keys=Object.keys(context).sort().join(',');
  if(context.kind==='personal' && keys==='kind') return;
  if(context.kind==='team' && keys==='kind,memberId,plan' && /^[a-f0-9]{32}$/.test(context.memberId) && ['plus','pro'].includes(context.plan)) return;
  throw new Error('请先选择有效成员与 Plus／Pro 模式。');
}

function createImports({send,choose,activity,sessions}) {
  let active=false;
  async function run(paths,context) {
    validateContext(context);
    if(active) throw new Error('另一批记录正在导入，请等待完成。');
    if(!Array.isArray(paths)||paths.length<1||paths.length>20||paths.some(p=>typeof p!=='string'||!path.isAbsolute(p)||p.length>4000)||Buffer.byteLength(JSON.stringify(paths))>100000) throw new Error('请拖入或选择 1–20 个本地文件或文件夹。');
    active=true;activity(true);
    try {
      const state=await send('status');
      if(context.kind==='personal') {
        if(state.quota.enabled) throw new Error('请先关闭额度守护，再导入个人记录。');
        return await send('quota.import',{paths},600000);
      }
      if(!state.team.members.some(m=>m.id===context.memberId)) throw new Error('成员不存在，请重新选择。');
      return await send('team.import.files',{paths,memberId:context.memberId,plan:context.plan},600000);
    } finally {active=false;activity(false)}
  }
  async function open(kind,context) {
    validateContext(context);
    if(!['files','folder'].includes(kind)) throw new Error('导入方式无效。');
    if(active) throw new Error('另一批记录正在导入，请等待完成。');
    const folder=kind==='folder';
    const title=folder?'选择年、月或日目录，递归导入其中的 JSONL':context.kind==='team'?'为选定成员导入 Codex JSONL 记录':'只选择属于此个人档案的 Codex 对话记录';
    const result=await choose({title,defaultPath:sessions,properties:folder?['openDirectory']:['openFile','multiSelections'],...(folder?{}:{filters:[{name:'Codex JSONL 对话记录',extensions:['jsonl']}]})});
    return result.canceled?null:run(result.filePaths,context);
  }
  return {run,open};
}
module.exports={createImports,validateContext};
