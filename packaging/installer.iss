; Smash Replay Recorder installer (Inno Setup 6). Built by packaging\build.ps1.
; One Windows permission prompt: installs the app, the USB sharing helper
; (usbipd-win) and WSL if missing, and the small Bluetooth service that lets
; the app switch the adapter over and back without further prompts.

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{6E4C3B1A-7F2D-4C8E-9A51-3D2B8F0C7E14}
AppName=Smash Replay Recorder
AppVersion={#AppVersion}
AppPublisher=Smash Replay Recorder
DefaultDirName={autopf}\Smash Replay Recorder
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
OutputDir=..\dist
OutputBaseFilename=SmashReplayRecorder-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
InfoBeforeFile=before-install.txt
UninstallDisplayIcon={app}\SmashReplayRecorder.exe
CloseApplications=yes

[Files]
Source: "..\dist\SmashReplayRecorder\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "..\build\service\SmashRecorderService.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Smash Replay Recorder"; Filename: "{app}\SmashReplayRecorder.exe"
Name: "{autodesktop}\Smash Replay Recorder"; Filename: "{app}\SmashReplayRecorder.exe"

[Run]
Filename: "msiexec.exe"; Parameters: "/i ""{app}\_internal\vendor\usbipd-win_5.3.0_x64.msi"" /qn /norestart"; \
  Check: not UsbipdInstalled; StatusMsg: "Installing the USB sharing helper..."; Flags: runhidden waituntilterminated
Filename: "{sys}\wsl.exe"; Parameters: "--install --no-distribution"; \
  Check: NeedsWsl; StatusMsg: "Installing Windows Subsystem for Linux (this can take a few minutes)..."; Flags: runhidden waituntilterminated
Filename: "{sys}\sc.exe"; Parameters: "create SmashRecorderBluetooth binPath= ""{app}\SmashRecorderService.exe"" start= auto DisplayName= ""Smash Replay Recorder Bluetooth"""; \
  StatusMsg: "Setting up Bluetooth access..."; Flags: runhidden waituntilterminated
Filename: "{sys}\sc.exe"; Parameters: "description SmashRecorderBluetooth ""Lets Smash Replay Recorder use this PC's Bluetooth adapter while recording and gives it back to Windows afterwards."""; Flags: runhidden waituntilterminated
Filename: "{sys}\sc.exe"; Parameters: "start SmashRecorderBluetooth"; Flags: runhidden waituntilterminated
Filename: "{app}\SmashReplayRecorder.exe"; Description: "Open Smash Replay Recorder"; \
  Flags: postinstall nowait skipifsilent runasoriginaluser; Check: not NeedRestart

[UninstallRun]
Filename: "{sys}\sc.exe"; Parameters: "stop SmashRecorderBluetooth"; Flags: runhidden waituntilterminated; RunOnceId: "StopService"
Filename: "{sys}\sc.exe"; Parameters: "delete SmashRecorderBluetooth"; Flags: runhidden waituntilterminated; RunOnceId: "DeleteService"
Filename: "{sys}\wsl.exe"; Parameters: "--unregister SmashRecorder"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveHelper"

[Code]
var
  WslMissing: Boolean;

function UsbipdInstalled: Boolean;
begin
  Result := FileExists(ExpandConstant('{autopf}\usbipd-win\usbipd.exe'));
end;

function InitializeSetup: Boolean;
var
  Code: Integer;
begin
  { wsl.exe exists as a stub on PCs without WSL; --version only succeeds when installed. }
  WslMissing := not (Exec(ExpandConstant('{sys}\wsl.exe'), '--version', '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0));
  Result := True;
end;

function NeedsWsl: Boolean;
begin
  Result := WslMissing;
end;

function NeedRestart: Boolean;
begin
  Result := WslMissing;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  { Upgrades: the running service locks its exe; stopping it also returns Bluetooth to Windows. }
  Exec(ExpandConstant('{sys}\sc.exe'), 'stop SmashRecorderBluetooth', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(1500);
  Result := '';
end;

