param(
    [Parameter(Mandatory=$true)][string]$Python,
    # Inputs downloaded into vendor\ (see BUILDING.md): ffmpeg (LGPL build),
    # the signed usbipd-win installer (checksum verified below) and the helper
    # image made by packaging/build_helper_image.sh.
    [string]$FfmpegDir = 'vendor\ffmpeg',
    [string]$UsbipdMsi = 'vendor\usbipd-win_5.3.0_x64.msi',
    [string]$HelperImage = 'vendor\smash-recorder-helper.tar.gz',
    [string]$Iscc = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    [string]$Version = '0.1.0'
)
# Builds dist\SmashReplayRecorder-Setup.exe: the app, the Bluetooth service,
# ffmpeg, usbipd-win and the controller helper image.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    & $Python -c "import tkinter; tkinter.Tcl()"
    if ($LASTEXITCODE -ne 0) { throw 'Python Tcl/Tk runtime is unavailable; refusing to build a GUI executable without it.' }
    if (-not (Test-Path (Join-Path $FfmpegDir 'ffmpeg.exe'))) { throw "ffmpeg.exe not found in $FfmpegDir" }
    $msiHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $UsbipdMsi).Hash
    if ($msiHash -ne '1C984914AEC944DE19B64EFF232421439629699F8138E3DDC29301175BC6D938') { throw 'usbipd installer checksum mismatch.' }
    if (-not (Test-Path $HelperImage)) { throw "Helper image not found: $HelperImage (run packaging/build_helper_image.sh)" }

    # 1. The Bluetooth service (plain .NET Framework, present on every Windows 10/11 PC).
    New-Item -ItemType Directory -Force build\service | Out-Null
    & "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo /optimize /target:exe `
        /out:build\service\SmashRecorderService.exe /r:System.ServiceProcess.dll /r:System.Web.Extensions.dll `
        /r:System.Core.dll packaging\service\SmashRecorderService.cs
    if ($LASTEXITCODE -ne 0) { throw 'Service build failed.' }

    # 2. The app, as a folder (fast start; nothing unpacked at launch).
    $sep = ';'
    # PyInstaller logs to stderr; judge success by its exit code only.
    $ErrorActionPreference = 'Continue'
    & $Python -m PyInstaller --noconfirm --onedir --windowed --noupx --name SmashReplayRecorder `
        --paths $projectRoot --specpath build/pyinstaller --workpath build/pyinstaller/work --distpath dist `
        --copy-metadata websocket-client `
        --add-data "$projectRoot/recorder_app/assets/screens.npz${sep}recorder_app/assets" `
        --add-data "$projectRoot/recorder_app/helper/switch_bridge.py${sep}recorder_app/helper" `
        --add-binary "$(Resolve-Path (Join-Path $FfmpegDir 'ffmpeg.exe'))${sep}ffmpeg" `
        --add-data "$(Resolve-Path $UsbipdMsi)${sep}vendor" `
        --add-data "$(Resolve-Path $HelperImage)${sep}vendor" `
        --add-data "$projectRoot/THIRD_PARTY_NOTICES.md${sep}." `
        packaging/entry.py
    if ($LASTEXITCODE -ne 0) { throw 'App build failed.' }
    $ErrorActionPreference = 'Stop'

    # 3. The installer.
    & $Iscc /Q "/DAppVersion=$Version" packaging\installer.iss
    if ($LASTEXITCODE -ne 0) { throw 'Installer build failed.' }
    Get-FileHash -Algorithm SHA256 -LiteralPath dist\SmashReplayRecorder-Setup.exe
} finally {
    Pop-Location
}
