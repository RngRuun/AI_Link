const {test}=require('node:test'),assert=require('node:assert/strict');
const {connectionParams}=require('../electron/connection.cjs');
test('old local connection remains compatible',()=>assert.deepEqual(connectionParams({shared:false},null),{shared:false}));
test('native credential reference resolves only inside main process',()=>assert.deepEqual(connectionParams({shared:true,endpoint:'ws://127.0.0.1:4500',tokenRef:'opaque'}, {ref:'opaque',path:'C:/fixture/shared.token'}),{shared:true,endpoint:'ws://127.0.0.1:4500',tokenFile:'C:/fixture/shared.token'}));
test('renderer cannot pass arbitrary credential paths or invented refs',()=>{
  for(const params of [{shared:true,tokenFile:'C:/private.txt'},{shared:true,endpoint:'ws://127.0.0.1:4500',tokenRef:'invented'},{shared:false,tokenRef:'opaque'}])assert.throws(()=>connectionParams(params,{ref:'opaque',path:'C:/fixture/shared.token'}));
});
test('shared destinations must be loopback without embedded secrets',()=>{
  for(const endpoint of ['ws://example.com:4500','ws://0.0.0.0:4500','ws://user:secret@127.0.0.1:4500','ws://127.0.0.1:4500?token=secret','ws://127.0.0.1:4500/path'])assert.throws(()=>connectionParams({shared:true,endpoint},null));
});
