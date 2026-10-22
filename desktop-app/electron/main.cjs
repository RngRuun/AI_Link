const { app, BrowserWindow, protocol, net, ipcMain, dialog, Menu, shell } = require('electron');
const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs/promises');
const { pathToFileURL } = require('node:url');
const { validateCommand, trustedFrame } = require('./policy.cjs');
const { createImports } = require('./imports.cjs');
const {connectionParams}=require('./connection.cjs');
const {randomUUID}=require('node:crypto');
const {createRetention}=require('./retention.cjs');
const {profilePaths}=require('./profile.cjs');
let selectedCredential=null;
protocol.registerSchemesAsPrivileged([{ scheme: 'ailink', privileges: { standard: true, secure: true, supportFetchAPI: true } }]);
app.setAppUserModelId('AIExperience.AILink');
const appRoot = path.resolve(__dirname, '..');
const distributionProfile=profilePaths(require(path.join(appRoot,'package.json')),process.env,{appData:app.getPath('appData'),localData:process.env.LOCALAPPDATA||app.getPath('appData')});
if(distributionProfile.userData){require('node:fs').mkdirSync(distributionProfile.userData,{recursive:true});app.setPath('userData',distributionProfile.userData)}
let window, child, ready = false, closing = false, sequence = 0;
let backendError = '后台服务正在启动…';
const pending = new Map();
const siblingBackend = path.join(appRoot, 'backend', 'AI Link Service.exe');
function send(method, params = {}, timeout = 60000) {
  return new Promise((resolve, reject) => {
    if (!child || child.exitCode !== null || (!ready && method !== 'shutdown')) return reject(new Error(backendError));
    const id = ++sequence;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('操作超时，执行结果可能未知，请刷新状态。')); }, timeout);
    pending.set(id, { resolve, reject, timer });
    child.stdin.write(JSON.stringify({ id, method, params }) + '\n', error => {
      if (error) { clearTimeout(timer); pending.delete(id); reject(error); }
    });
  });
}
function startBackend() {
  const localPython=path.join(appRoot,'..','.venv','Scripts','python.exe');
  const executable = process.env.AI_LINK_DEV_PYTHON || (require('node:fs').existsSync(localPython)?localPython:'python');
  const useSource = !require('node:fs').existsSync(siblingBackend);
  const args = useSource ? [path.resolve(appRoot, '..', 'desktop_worker.py')] : [];
  // Development override is main-process-only; never exposed through the renderer bridge.
  if (distributionProfile.state) args.push('--state-dir', distributionProfile.state);
  child = spawn(useSource ? executable : siblingBackend, args, { windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
  let buffer = '';
  child.stdout.setEncoding('utf8');
  child.stdout.on('data', data => {
    buffer += data;
    if (buffer.length > 20000000) { backendError = '后台消息超出大小限制。'; child.kill(); return; }
    let boundary;
    while ((boundary = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 1);
      try {
        const value = JSON.parse(line);
        if (value.event === 'ready') {
          ready = true; backendError = '';
          // Explicit relaunch flag used only when restoring an already-enabled mode.
          if (process.argv.includes('--resume-network')) void send('network.enable').catch(error => { backendError=error.message; });
        }
        else if (value.event === 'fatal') { backendError = value.error; ready = false; }
        else if (pending.has(value.id)) {
          const item = pending.get(value.id); pending.delete(value.id); clearTimeout(item.timer);
          value.error ? item.reject(new Error(value.error)) : item.resolve(value.result);
        }
      } catch { /* Ignore non-protocol diagnostics; never execute child output. */ }
    }
  });
  child.stderr.on('data', () => {});
  function unavailable(error) {
    ready = false;
    backendError = error?.message || backendError || '后台服务已关闭，请重启应用。独立守护会恢复代理。';
    for (const item of pending.values()) { clearTimeout(item.timer); item.reject(new Error(backendError)); }
    pending.clear();
  }
  child.on('error', unavailable);
  child.on('exit', () => unavailable());
}
function assertTrusted(event) { if (!trustedFrame(event, window)) throw new Error('拒绝来自其他窗口的请求。'); }
if (!app.requestSingleInstanceLock()) { app.quit(); }
else {
  app.on('second-instance', () => { if (window) { if (window.isMinimized()) window.restore(); window.focus(); } });
  app.whenReady().then(async () => {
    Menu.setApplicationMenu(null);
    const dist = path.join(appRoot, 'dist');
    protocol.handle('ailink', request => {
      const url = new URL(request.url);
      let name;
      try { name = decodeURIComponent(url.pathname); } catch { return new Response('Invalid path', { status: 400 }); }
      const file = path.resolve(dist, '.' + (name === '/' ? '/index.html' : name));
      if (url.host !== 'app' || !file.startsWith(dist + path.sep)) return new Response('Forbidden', { status: 403 });
      const types = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png', '.woff2': 'font/woff2' };
      return fs.readFile(file).then(data => new Response(data, { headers: { 'Content-Type': types[path.extname(file)] || 'application/octet-stream', 'X-Content-Type-Options': 'nosniff' } }))
        .catch(() => new Response('Not found', { status: 404 }));
    });
    window = new BrowserWindow({ width: 1280, height: 850, minWidth: 560, minHeight: 480,
      title: 'AI Link · AI 用户体验优化', backgroundColor: '#f3f6fa', show: false,
      icon: path.join(appRoot, 'assets', 'ai-link.ico'), titleBarStyle: 'hidden',
      titleBarOverlay: { color: '#f3f6fa', symbolColor: '#263142', height: 42 },
      webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, nodeIntegration: false,
        sandbox: true, webSecurity: true, devTools: !app.isPackaged }
    });
    window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    window.webContents.on('will-navigate', event => event.preventDefault());
    window.webContents.on('will-attach-webview', event => event.preventDefault());
    window.webContents.session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    window.webContents.session.setPermissionCheckHandler(() => false);
    window.webContents.session.webRequest.onBeforeRequest((details, callback) => callback({ cancel: !details.url.startsWith('ailink://app/') }));
    ipcMain.handle('service:request', async (event, method, params) => {
      assertTrusted(event); validateCommand(method, params);
      if (closing) throw new Error('应用正在退出。');
      if(method==='quota.connect')return send(method,connectionParams(params,selectedCredential));
      return send(method, params);
    });
    ipcMain.handle('dialog:codex-token',async event=>{
      assertTrusted(event);
      const result=await dialog.showOpenDialog(window,{title:'选择本机共享服务的连接凭据文件',properties:['openFile']});
      if(result.canceled||result.filePaths.length!==1)return null;
      const file=result.filePaths[0],info=await fs.stat(file);
      if(!info.isFile()||info.size>16384)throw new Error('连接凭据必须是小于 16 KB 的 token 文件。');
      selectedCredential={ref:randomUUID(),path:file};
      return {ref:selectedCredential.ref,name:path.basename(file)};
    });
    ipcMain.handle('dialog:directory', async event => {
      assertTrusted(event);
      const result = await dialog.showOpenDialog(window, { title: '选择只读任务的工作目录', properties: ['openDirectory'] });
      return result.canceled ? null : result.filePaths[0];
    });
    const retention=createRetention({send,choose:options=>dialog.showOpenDialog(window,options),confirm:options=>dialog.showMessageBox(window,options),
      openPath:value=>shell.openPath(value),openExternal:value=>shell.openExternal(value),clearCache:()=>window.webContents.session.clearCache(),cacheSize:()=>window.webContents.session.getCacheSize()});
    for(const [channel,method] of [['storage:import','importCloud'],['storage:location','location'],['storage:purge','purge'],['storage:cloud','cloud'],['storage:cache','cache']]){
      ipcMain.handle(channel,(event,...args)=>{assertTrusted(event);if(closing)throw new Error('应用正在退出。');return retention[method](...args)});
    }
    ipcMain.handle('appearance:theme', async (event, dark) => {
      assertTrusted(event);
      if (typeof dark !== 'boolean') throw new Error('无效外观参数。');
      window.setTitleBarOverlay({color: dark ? '#111a27' : '#f3f6fa',symbolColor:dark?'#d9e2ed':'#263142',height:42});
    });
    const imports=createImports({send,choose:options=>dialog.showOpenDialog(window,options),sessions:path.join(process.env.CODEX_HOME || path.join(app.getPath('home'),'.codex'),'sessions'),activity:active=>{if(window&&!window.isDestroyed())window.webContents.send('imports:activity',active)}});
    ipcMain.handle('dialog:personal-import',(event,kind='files')=>{assertTrusted(event);return imports.open(kind,{kind:'personal'})});
    function teamOptions(memberId, plan) {
      if (typeof memberId !== 'string' || !/^[a-f0-9]{32}$/.test(memberId) || !['plus','pro'].includes(plan)) throw new Error('成员或模式无效。');
    }
    ipcMain.handle('dialog:team-import',(event,memberId,plan,kind='files')=>{assertTrusted(event);return imports.open(kind,{kind:'team',memberId,plan})});
    ipcMain.handle('imports:drop',(event,paths,context)=>{assertTrusted(event);return imports.run(paths,context)});
    ipcMain.handle('dialog:team-export',async(event,plan,period,format)=>{
      assertTrusted(event);
      if(!['plus','pro'].includes(plan)||!['primary','secondary','all'].includes(period)||!['svg','csv'].includes(format))throw new Error('导出选项无效。');
      const report=await send('team.export',{plan,window:period,format});
      const result=await dialog.showSaveDialog(window,{title:format==='svg'?'导出多人用量饼图':'导出多人用量数据',defaultPath:`AI-Link-多人用量-${plan.toUpperCase()}-${period}.${format}`,filters:[{name:format.toUpperCase(),extensions:[format]}]});
      if(result.canceled || !result.filePath)return false;
      await fs.writeFile(result.filePath,report.data,'utf8');return true;
    });
    ipcMain.handle('dialog:export', async event => {
      assertTrusted(event);
      const report = await send('report');
      const result = await dialog.showSaveDialog(window, { title: '导出诊断报告', defaultPath: 'AI-Link-诊断报告.json', filters: [{ name: 'JSON', extensions: ['json'] }] });
      if (result.canceled || !result.filePath) return false;
      await fs.writeFile(result.filePath, JSON.stringify(report, null, 2), 'utf8');
      return true;
    });
    startBackend();
    window.once('ready-to-show', () => window.show());
    window.webContents.on('render-process-gone', () => app.quit());
    window.on('close', event => { if (!closing) { event.preventDefault(); app.quit(); } });
    await window.loadURL('ailink://app/index.html');
    window.show();
  }).catch(error => { dialog.showErrorBox('AI Link 启动失败', error.message); app.quit(); });
  app.on('before-quit', event => {
    if (closing) return;
    event.preventDefault(); closing = true;
    if (window && !window.isDestroyed()) window.webContents.send('app:closing');
    const restore = ready ? send('shutdown', {}, 18000) : Promise.resolve();
    restore.catch(() => {}).finally(() => { child?.stdin.end(); child?.kill(); window?.destroy(); app.quit(); });
  });
  app.on('window-all-closed', () => app.quit());
}
