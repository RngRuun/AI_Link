const { spawn } = require('node:child_process');
const path = require('node:path');
const child = spawn(require('electron'), [path.resolve(__dirname, '..', 'tests', 'render.cjs')], { stdio: 'inherit', windowsHide: true });
child.on('error', error => { console.error(error); process.exitCode = 1; });
child.on('exit', code => process.exitCode = code || 0);
