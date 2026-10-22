const path=require('node:path');
function profilePaths(manifest,env,roots){
  const mode=manifest.aiLinkProfile;
  if(mode!==undefined&&!/^fresh-\d+\.\d+\.\d+$/.test(mode))throw new Error('Invalid distribution profile.');
  return {
    userData:env.AI_LINK_PROFILE_DIR?path.resolve(env.AI_LINK_PROFILE_DIR):mode?path.join(roots.appData,'AI Link',mode):null,
    state:env.AI_LINK_STATE_DIR?path.resolve(env.AI_LINK_STATE_DIR):mode?path.join(roots.localData,'AIExperience',mode):null
  };
}
module.exports={profilePaths};
