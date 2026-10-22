const { spawn } = require('node:child_process');
const path = require('node:path');
const child = spawn(require('electron'), [path.resolve(__dirname, '..', 'tests', 'render.cjs')], { stdio: 'inherit', windowsHide: true });
const deadline = setTimeout(() => { console.error('Renderer verification exceeded 180 seconds'); child.kill(); process.exitCode = 1; }, 180000);
child.on('error', error => { clearTimeout(deadline); console.error(error); process.exitCode = 1; });
child.on('exit', (code, signal) => { clearTimeout(deadline); process.exitCode = signal ? 1 : (code ?? 1); });
