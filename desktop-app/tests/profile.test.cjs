const {test}=require('node:test'),assert=require('node:assert/strict'),path=require('node:path');
const {profilePaths}=require('../electron/profile.cjs');
const roots={appData:path.resolve('fixture-roaming'),localData:path.resolve('fixture-local')};
test('normal source launch preserves default state behavior',()=>assert.deepEqual(profilePaths({}, {},roots),{userData:null,state:null}));
test('fresh release isolates both preferences and backend records',()=>{const p=profilePaths({aiLinkProfile:'fresh-0.13.0'},{},roots);assert.equal(p.state,path.join(roots.localData,'AIExperience','fresh-0.13.0'));assert.equal(p.userData,path.join(roots.appData,'AI Link','fresh-0.13.0'))});
test('explicit profiles override fresh paths and malformed release profiles fail',()=>{const p=profilePaths({aiLinkProfile:'fresh-0.13.0'},{AI_LINK_STATE_DIR:'fixture-state',AI_LINK_PROFILE_DIR:'fixture-profile'},roots);assert.equal(p.state,path.resolve('fixture-state'));assert.equal(p.userData,path.resolve('fixture-profile'));assert.throws(()=>profilePaths({aiLinkProfile:'../../secret'},{},roots))});
