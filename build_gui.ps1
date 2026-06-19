$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvDir = Join-Path $Root ".gui-venv"
$Python = Join-Path $EnvDir "Scripts\python.exe"
$Uv = Get-Command uv -ErrorAction SilentlyContinue

if (-not (Test-Path -LiteralPath $Python)) {
    if ($Uv) {
        & $Uv.Source venv --python 3.12 $EnvDir
    } else {
        $PyLauncher = Get-Command py -ErrorAction SilentlyContinue
        if ($PyLauncher) {
            & $PyLauncher.Source -3.12 -m venv $EnvDir
        } else {
            & python -m venv $EnvDir
        }
    }
}

if ($Uv) {
    & $Uv.Source pip install --python $Python -r (Join-Path $Root "requirements-gui.txt")
} else {
    & $Python -m pip install -r (Join-Path $Root "requirements-gui.txt")
}
& $Python (Join-Path $Root "socd_controller.py") --self-test
if ($LASTEXITCODE -ne 0) { throw "SOCD controller self-test failed." }

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --noconsole `
    --name "SOCD_Controller" `
    --distpath $Root `
    --workpath (Join-Path $Root "build") `
    --specpath (Join-Path $Root "build") `
    (Join-Path $Root "socd_controller.py")

if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }
Write-Host (Join-Path $Root "SOCD_Controller.exe")
