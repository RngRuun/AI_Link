const fs = require('node:fs/promises');
const path = require('node:path');
async function main() {
  const appRoot = path.resolve(__dirname, '..');
  const args=process.argv.slice(2),positions=args.filter(a=>!a.startsWith('--'));
  if(args.some(a=>a.startsWith('--')&&a!=='--fresh-profile'))throw new Error('Unknown packaging option');
  const fresh=args.includes('--fresh-profile');
  const output = path.resolve(positions[0] || path.join(appRoot, '..', 'dist-vue'));
  const backend = path.resolve(positions[1] || path.join(appRoot, '..', '.build-vue', 'backend-dist', 'AI Link Service'));
  const runtime = path.join(appRoot, 'node_modules', 'electron', 'dist');
  const application = path.join(output, 'AI Link');
  await fs.access(path.join(backend, 'AI Link Service.exe'));
  await fs.access(path.join(appRoot, 'dist', 'index.html'));
  await fs.mkdir(application, { recursive: true });
  for (const entry of await fs.readdir(runtime, { withFileTypes: true })) {
    if (entry.name === 'electron.exe') continue;
    await fs.cp(path.join(runtime, entry.name), path.join(application, entry.name), { recursive: true });
  }
  const exe = path.join(application, 'AI Link.exe');
  await fs.copyFile(path.join(runtime, 'electron.exe'), exe);
  const target = path.join(application, 'resources', 'app');
  await fs.mkdir(target, { recursive: true });
  const manifest = require(path.join(appRoot, 'package.json'));
  await fs.writeFile(path.join(target, 'package.json'), JSON.stringify({ name: manifest.name, productName: manifest.productName, version: manifest.version, main: 'electron/main.cjs',...(fresh?{aiLinkProfile:'fresh-'+manifest.version}:{}) }, null, 2));
  await fs.cp(path.join(appRoot, 'dist'), path.join(target, 'dist'), { recursive: true });
  await fs.cp(path.join(appRoot, 'electron'), path.join(target, 'electron'), { recursive: true });
  await fs.mkdir(path.join(target, 'assets'), { recursive: true });
  await fs.copyFile(path.join(appRoot, '..', 'assets', 'ai-link.ico'), path.join(target, 'assets', 'ai-link.ico'));
  await fs.cp(backend, path.join(target, 'backend'), { recursive: true });
  const { rcedit } = await import('rcedit');
  await rcedit(exe, { 'file-version': manifest.version+'.0', 'product-version': manifest.version+'.0',
    'version-string': { ProductName: 'AI Link', FileDescription: 'AI Link · AI Experience', CompanyName: 'AI Experience', OriginalFilename: 'AI Link.exe', InternalName: 'AI Link' },
    icon: path.join(appRoot, '..', 'assets', 'ai-link.ico') });
  const restoreDefault = fresh ? `"%LOCALAPPDATA%\\AIExperience\\fresh-${manifest.version}"` : '';
  const restoreScript = '@echo off\r\ncd /d "%~dp0"\r\n'
    + 'if defined AI_LINK_STATE_DIR (\r\n  "resources\\app\\backend\\AI Link Service.exe" --restore --state-dir "%AI_LINK_STATE_DIR%"\r\n'
    + ') else (\r\n  "resources\\app\\backend\\AI Link Service.exe" --restore' + (restoreDefault ? ' --state-dir ' + restoreDefault : '') + '\r\n)\r\npause\r\n';
  await fs.writeFile(path.join(application, '紧急恢复.cmd'), restoreScript, 'ascii');
  await fs.copyFile(path.join(appRoot, '..', 'README.md'), path.join(application, '使用说明.md'));
  await fs.copyFile(path.join(appRoot, '..', 'SECURITY.md'), path.join(application, 'SECURITY.md'));
  for(const name of ['architecture.md','features.md','storage-cleanup.md','quota-guard.md','test-report.md','images']){
    await fs.cp(path.join(appRoot,'..','docs',name),path.join(application,'docs',name),{recursive:true});
  }
  console.log('Portable desktop:', exe);
}
main().catch(error => { console.error(error); process.exitCode = 1; });
