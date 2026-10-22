param([string]$Python='',[switch]$TestCodex)
$ErrorActionPreference='Stop'
$projectRoot=Split-Path -Parent $PSScriptRoot
if(-not $Python){$candidate=Join-Path $projectRoot '.venv/Scripts/python.exe';$Python=if(Test-Path -LiteralPath $candidate){$candidate}else{'python'}}
Push-Location $projectRoot
try {
  & $Python -m unittest discover -s tests -v
  if($LASTEXITCODE -ne 0){throw 'Backend tests failed'}
  & $Python tests/integration_network_modes.py .artifacts/network.json
  if($LASTEXITCODE -ne 0){throw 'Local socket integration failed'}
  Push-Location desktop-app
  try {
    npm.cmd ci
    if($LASTEXITCODE -ne 0){throw 'Dependency installation failed'}
    npm.cmd run build
    if($LASTEXITCODE -ne 0){throw 'Frontend compilation failed'}
    npm.cmd test
    if($LASTEXITCODE -ne 0){throw 'IPC tests failed'}
    node scripts/test-render.cjs
    if($LASTEXITCODE -ne 0){throw 'Renderer tests failed'}
  } finally {Pop-Location}
  if($TestCodex){
    foreach($case in @('integration_codex_lifecycle','integration_existing_shared','integration_storage_cleanup','integration_control_scope')){
      & $Python "tests/$case.py" ".artifacts/$case.json"
      if($LASTEXITCODE -ne 0){throw "Codex integration failed: $case"}
    }
  }
} finally {Pop-Location}
