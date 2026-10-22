param([string]$Python='', [switch]$FreshProfile)
$ErrorActionPreference = 'Stop'
$sourceRoot = $PSScriptRoot
if (-not $Python) { $candidate=Join-Path $sourceRoot '.venv/Scripts/python.exe'; $Python=if(Test-Path -LiteralPath $candidate){$candidate}else{'python'} }
$frontendRoot = Join-Path $sourceRoot 'desktop-app'
$buildRoot = Join-Path $sourceRoot '.build-vue'
$backendDist = Join-Path $buildRoot 'backend-dist'
Push-Location $frontendRoot
try {
    npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
    node node_modules/electron/install.js
    if ($LASTEXITCODE -ne 0) { throw 'Electron runtime download failed' }
    node -e "require('electron')"
    if ($LASTEXITCODE -ne 0) { throw 'Electron runtime installation failed' }
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
} finally { Pop-Location }
& $Python -m pip install --upgrade --target "$buildRoot\python-deps" websocket-client==1.9.0
if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed' }
& $Python -m PyInstaller --paths "$buildRoot\python-deps" --noconfirm --onedir --console --name 'AI Link Service' --icon "$sourceRoot\assets\ai-link.ico" --exclude-module PyQt5 --exclude-module PyQt6 --exclude-module PySide6 --distpath $backendDist --workpath "$buildRoot\backend-build" --specpath $buildRoot "$sourceRoot\desktop_worker.py"
if ($LASTEXITCODE -ne 0) { throw 'Backend build failed' }
Push-Location $frontendRoot
try {
    $packageArguments=@('scripts/package.cjs',"$sourceRoot\dist-vue","$backendDist\AI Link Service")
    if ($FreshProfile) {$packageArguments+='--fresh-profile'}
    & node @packageArguments
    if ($LASTEXITCODE -ne 0) { throw 'Desktop packaging failed' }
} finally { Pop-Location }
Write-Output "Portable application: $sourceRoot\dist-vue\AI Link\AI Link.exe"
