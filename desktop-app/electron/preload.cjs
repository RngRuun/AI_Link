const { contextBridge, ipcRenderer, webUtils } = require('electron');
contextBridge.exposeInMainWorld('aiLink', {
  request: (method, params = {}) => ipcRenderer.invoke('service:request', method, params),
  exportReport: () => ipcRenderer.invoke('dialog:export'),
  chooseDirectory: () => ipcRenderer.invoke('dialog:directory'),
  chooseCodexCredential:()=>ipcRenderer.invoke('dialog:codex-token'),
  importCloudExport:label=>ipcRenderer.invoke('storage:import',label),
  openStorageLocation:kind=>ipcRenderer.invoke('storage:location',kind),
  purgeStorageBatch:batchId=>ipcRenderer.invoke('storage:purge',batchId),
  openCloudCleanup:(id=null)=>ipcRenderer.invoke('storage:cloud',id),
  appCache:(clear=false)=>ipcRenderer.invoke('storage:cache',clear),
  importPersonal: (kind='files') => ipcRenderer.invoke('dialog:personal-import', kind),
  importTeam: (memberId, plan, kind='files') => ipcRenderer.invoke('dialog:team-import', memberId, plan, kind),
  importDropped: (files, context) => {
    if(!Array.isArray(files)||files.length<1||files.length>20) return Promise.reject(new Error('请每次拖入 1–20 个文件或文件夹。'));
    const paths=files.map(file=>webUtils.getPathForFile(file));
    if(paths.some(p=>!p)) return Promise.reject(new Error('请从文件资源管理器拖入本地 JSONL 或文件夹。'));
    return ipcRenderer.invoke('imports:drop', paths, context);
  },
  exportTeam: (plan, window, format) => ipcRenderer.invoke('dialog:team-export', plan, window, format),
  setTheme: dark => ipcRenderer.invoke('appearance:theme', dark),
  onImportActivity: callback => {const listener=(_event,active)=>callback(active===true);ipcRenderer.on('imports:activity',listener);return ()=>ipcRenderer.removeListener('imports:activity',listener)},
  onClosing: callback => { const listener = () => callback(); ipcRenderer.on('app:closing', listener); return () => ipcRenderer.removeListener('app:closing', listener); }
});
