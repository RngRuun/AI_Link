const test=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const {createImports,validateContext}=require('../electron/imports.cjs');
const member='a'.repeat(32),root=path.resolve('fixture-sessions');
function setup(overrides={}) {
  const calls=[],activity=[],dialogs=[];
  const state={quota:{enabled:false},team:{members:[{id:member}]}};
  const controller=createImports({sessions:root,send:async(method,params,timeout)=>{calls.push({method,params,timeout});if(method==='status')return state;return {files:27,tokens:123}},activity:value=>activity.push(value),choose:async options=>{dialogs.push(options);return {canceled:false,filePaths:[root]}},...overrides});
  return {controller,calls,activity,dialogs,state};
}
test('directory chooser sends selected root once and retains team ownership',async()=>{
  const {controller,calls,dialogs,activity}=setup();const result=await controller.open('folder',{kind:'team',memberId:member,plan:'pro'});
  assert.deepEqual(dialogs[0].properties,['openDirectory']);assert.equal(dialogs[0].defaultPath,root);
  assert.deepEqual(calls[1].params,{paths:[root],memberId:member,plan:'pro'});assert.equal(calls[1].timeout,600000);assert.equal(result.files,27);assert.deepEqual(activity,[true,false]);
});
test('cancelled chooser does not import or announce activity',async()=>{
  const {controller,calls,activity}=setup({choose:async()=>({canceled:true,filePaths:[]})});
  assert.equal(await controller.open('files',{kind:'personal'}),null);assert.equal(calls.length,0);assert.equal(activity.length,0);
});
test('source failure clears activity and permits a later retry',async()=>{
  let fail=true;const states=[];const {controller}=setup({activity:v=>states.push(v),send:async method=>{if(method==='status')return {quota:{enabled:false},team:{members:[{id:member}]}};if(fail)throw new Error('bad encoding');return {files:1,tokens:0}}});
  await assert.rejects(controller.run([root],{kind:'personal'}),/bad encoding/);fail=false;
  await controller.run([root],{kind:'personal'});assert.deepEqual(states,[true,false,true,false]);
});
test('second batch is refused while the first is pending',async()=>{
  let finish;const {controller}=setup({send:async method=>method==='status'?{quota:{enabled:false}}:new Promise(resolve=>finish=resolve)});
  const first=controller.run([root],{kind:'personal'});await new Promise(resolve=>setImmediate(resolve));
  await assert.rejects(controller.run([root],{kind:'personal'}),/正在导入/);finish({files:1,tokens:1});await first;
});
test('personal guard and unknown team member prevent import dispatch',async()=>{
  const {controller,state,calls}=setup();state.quota.enabled=true;
  await assert.rejects(controller.run([root],{kind:'personal'}),/关闭额度守护/);
  await assert.rejects(controller.run([root],{kind:'team',memberId:'b'.repeat(32),plan:'plus'}),/成员不存在/);
  assert(calls.every(c=>c.method==='status'));
});
test('drop options cannot smuggle arbitrary commands or relative paths',async()=>{
  for(const bad of [{kind:'team',memberId:member,plan:'plus',command:'exec'},{kind:'unknown'},null])assert.throws(()=>validateContext(bad));
  const {controller}=setup();await assert.rejects(controller.run(['relative.jsonl'],{kind:'personal'}));
});
