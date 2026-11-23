# Builds the installer: tests -> PyInstaller -> Inno Setup.   Usage: powershell -File build.ps1 [-SkipTests]
# Output: %USERPROFILE%\aaoverlay-build\installer\AAOdds-<version>-setup.exe (outside OneDrive on purpose).
param([switch]$SkipTests)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $env:USERPROFILE "aaoverlay-venv\Scripts\python.exe"
$build = Join-Path $env:USERPROFILE "aaoverlay-build"
$iscc = Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"
Set-Location $here

if (-not $SkipTests) {
    $env:QT_QPA_PLATFORM = "offscreen"
    & $py -m pytest tests -q --ignore=tests/test_frozen.py
    if ($LASTEXITCODE) { throw "overlay tests failed" }
    Set-Location (Split-Path -Parent $here)
    & $py -m pytest tests -q
    if ($LASTEXITCODE) { throw "calculator tests failed" }
    Set-Location $here
    Remove-Item Env:QT_QPA_PLATFORM
}

& $py -m PyInstaller aaoverlay.spec --noconfirm --distpath "$build\dist" --workpath "$build\work"
if ($LASTEXITCODE) { throw "PyInstaller failed" }

$version = (& $py -c "import aaoverlay; print(aaoverlay.__version__)").Trim()
& $iscc "/DAppVersion=$version" "/DSourceDir=$build\dist\AAOdds" "/DOutDir=$build\installer" installer.iss
if ($LASTEXITCODE) { throw "Inno Setup failed" }
# Prove the packaged app and the installer behave like the source tree (tests/test_frozen.py).
if (-not $SkipTests) {
    & $py -m pytest tests/test_frozen.py -q
    if ($LASTEXITCODE) { throw "packaged-build tests failed" }
}
Write-Host "Built $build\installer\AAOdds-$version-setup.exe"
