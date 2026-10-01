param(
    [string]$Payload = "vendor\payloads\windows-x86_64"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$BuildRoot = Join-Path $ProjectRoot "build\windows"
$StagedPayload = Join-Path $BuildRoot "payload"
$GuiDist = Join-Path $BuildRoot "gui-dist"
$CliDist = Join-Path $BuildRoot "cli-dist"
$WorkRoot = Join-Path $BuildRoot "work"
$SpecRoot = Join-Path $BuildRoot "spec"

if (Test-Path $BuildRoot) {
    Remove-Item -Recurse -Force $BuildRoot
}
New-Item -ItemType Directory -Force $BuildRoot, $WorkRoot, $SpecRoot | Out-Null

python scripts\stage_tools.py $Payload $StagedPayload
if ($LASTEXITCODE -ne 0) { throw "Tool payload validation failed" }

python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --name mp4_to_dcp_cli `
    --distpath $CliDist `
    --workpath (Join-Path $WorkRoot "cli") `
    --specpath $SpecRoot `
    mp4_to_dcp.py
if ($LASTEXITCODE -ne 0) { throw "CLI build failed" }

python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onedir `
    --name MP4-to-DCP `
    --distpath $GuiDist `
    --workpath (Join-Path $WorkRoot "gui") `
    --specpath $SpecRoot `
    app.py
if ($LASTEXITCODE -ne 0) { throw "GUI build failed" }

$AppDirectory = Join-Path $GuiDist "MP4-to-DCP"
Copy-Item (Join-Path $CliDist "mp4_to_dcp_cli.exe") $AppDirectory
Copy-Item (Join-Path $StagedPayload "*") $AppDirectory -Recurse -Force

$SourceDirectory = Join-Path $BuildRoot "application-source"
New-Item -ItemType Directory -Force $SourceDirectory | Out-Null
Copy-Item app.py, mp4_to_dcp.py, tool_discovery.py, LICENSE, README.md, `
    THIRD_PARTY_NOTICES.md, requirements-gui.txt $SourceDirectory
New-Item -ItemType Directory -Force `
    (Join-Path $SourceDirectory "scripts"), `
    (Join-Path $SourceDirectory "tests"), `
    (Join-Path $SourceDirectory "vendor") | Out-Null
Copy-Item -Path scripts\*.py, scripts\*.ps1 `
    -Destination (Join-Path $SourceDirectory "scripts")
Copy-Item -Path tests\*.py -Destination (Join-Path $SourceDirectory "tests")
Copy-Item -Path vendor\README.md, vendor\bundle-manifest.example.json `
    -Destination (Join-Path $SourceDirectory "vendor")
Compress-Archive `
    -Path (Join-Path $SourceDirectory "*") `
    -DestinationPath (Join-Path $AppDirectory "mp4-to-dcp-source.zip")

Write-Host "Windows application staged at: $AppDirectory"
Write-Host "Sign the EXEs and DLLs, then build and sign the installer."
