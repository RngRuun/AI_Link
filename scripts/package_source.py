"""Allowlisted GitHub source export. Build/test/state directories cannot enter it."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import zipfile

ROOT=Path(__file__).resolve().parents[1]
ROOT_FILES=['README.md','CONTRIBUTING.md','SECURITY.md','.gitignore','.gitattributes','pyproject.toml','requirements-dev.txt','build-vue-desktop.ps1','desktop_worker.py','启动 AI Link.cmd','紧急恢复.cmd']
DOCS=['architecture.md','features.md','storage-cleanup.md','quota-guard.md','test-report.md']
BLOCKED={'node_modules','__pycache__','.venv','.git','.artifacts','.build-vue','dist','dist-vue','runtime','profile','Cache','GPUCache','Code Cache'}

def sources(root):
    files=[root/p for p in ROOT_FILES]
    files.extend(root/'docs'/p for p in DOCS)
    for directory,suffixes in [('ailink',{'.py'}),('scripts',{'.py','.ps1'}),('tests',{'.py','.html'}),('desktop-app/src',{'.vue','.ts','.css'}),('desktop-app/electron',{'.cjs'}),('desktop-app/scripts',{'.cjs'}),('desktop-app/tests',{'.cjs'}),('.github',{'.yml','.md'}),('docs/images',{'.png'}),('assets',{'.ico'})]:
        base=root/directory
        if base.exists():files.extend(p for p in base.rglob('*') if p.is_file() and p.suffix in suffixes and not set(p.relative_to(root).parts)&BLOCKED)
    files.extend(root/'desktop-app'/p for p in ['package.json','package-lock.json','tsconfig.json','vite.config.ts','index.html'])
    return sorted(set(files))

def audit(root,files):
    for path in files:
        if not path.is_file() or path.is_symlink():raise ValueError(f'Missing or linked source: {path.name}')
        relative=path.relative_to(root)
        if set(relative.parts)&BLOCKED or path.suffix.lower() in ('.pyc','.log','.exe','.sqlite','.token'):raise ValueError(f'Forbidden artifact: {relative}')
        if path.suffix in ('.py','.ts','.vue','.cjs','.json','.md','.ps1','.cmd','.yml','.css','.toml','.txt'):
            value=path.read_text('utf-8')
            if re.search(r'(?:sk-proj-|sk-)[A-Za-z0-9_-]{32,}',value):raise ValueError(f'Potential API credential: {relative}')
            if re.search(r'[A-Z]:[\\/]+Users[\\/]+\d{5,}[\\/]',value,re.I):raise ValueError(f'Personal machine path: {relative}')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('output',type=Path);parser.add_argument('--stage-only',action='store_true');parser.add_argument('--root',type=Path,default=ROOT)
    args=parser.parse_args();root=args.root.resolve();files=sources(root);audit(root,files)
    if args.stage_only:
        target=args.output.resolve()
        if target.exists():raise ValueError('Refusing to overwrite an existing source directory')
        for path in files:
            destination=target/path.relative_to(root);destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,destination)
        print(json.dumps({'source_files':len(files),'directory':str(target)}));return
    target=args.output.resolve();version=json.loads((root/'desktop-app/package.json').read_text('utf-8'))['version'];prefix='AI-Link-'+version
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for path in files:archive.write(path,prefix+'/'+path.relative_to(root).as_posix())
    with zipfile.ZipFile(target) as archive:
        if archive.testzip():raise ValueError('ZIP CRC failed')
    result={'file':target.name,'version':version,'source_files':len(files),'bytes':target.stat().st_size,'sha256':hashlib.file_digest(target.open('rb'),'sha256').hexdigest(),'crc_verified':True,'cache_and_runtime_excluded':True}
    target.with_suffix('.checksums.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))

if __name__=='__main__':main()
