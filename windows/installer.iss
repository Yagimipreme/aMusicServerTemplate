; Inno Setup script for aMusicServer
; Build with:  iscc /DAppVersion=1.0.0 installer.iss
;
; Result: Output\Setup_aMusicServer_v<version>.exe
;
; Per-user install (no UAC prompt) into %LOCALAPPDATA%\Programs\aMusicServer.
; The same AppId across versions means subsequent installs upgrade in place.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName        "aMusicServer"
#define AppPublisher   "yagimipreme"
#define AppGitHub      "https://github.com/Yagimipreme/aMusicServerTemplate"
#define AppCodeberg    "https://codeberg.org/Lycka/musicServerTemplate"
#define AppId          "{{9E2D7DBA-9F1C-4F9C-AB6E-A86B49AC4E12}"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppGitHub}
AppSupportURL={#AppGitHub}/issues
AppUpdatesURL={#AppGitHub}/releases
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=Output
OutputBaseFilename=Setup_aMusicServer_v{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\aMusicServer.exe
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts"
Name: "autostart";   Description: "Start {#AppName} when I sign in";       GroupDescription: "Startup"

[Files]
; PyInstaller output -> {app}
Source: "..\dist\aMusicServer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; Plain-text example config (user copies / edits config.json)
Source: "..\config.example.json"; DestDir: "{app}"; Flags: onlyifdoesntexist

; Browser extension folder lands at a stable, user-visible location for
; "Load Unpacked" — same files Inno ships, just exposed for browser pickup.
Source: "..\sWebExt\*"; DestDir: "{userdocs}\aMusicServer\sWebExt"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
Name: "{app}\logs";       Permissions: users-modify
Name: "{app}\playlists";  Permissions: users-modify

[Icons]
Name: "{group}\{#AppName}";               Filename: "{app}\aMusicServer.exe"
Name: "{group}\Open music folder";        Filename: "{app}\aMusicServer.exe"; Comment: "Opens tray menu"
Name: "{group}\Browser extension folder"; Filename: "{userdocs}\aMusicServer\sWebExt"
Name: "{group}\Uninstall {#AppName}";     Filename: "{uninstallexe}"
Name: "{commondesktop}\{#AppName}";       Filename: "{app}\aMusicServer.exe"; Tasks: desktopicon

[Registry]
; "Start with Windows" — created if user ticked the task. Tray app can also
; toggle this at runtime via HKCU\...\Run\aMusicServer.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "aMusicServer"; ValueData: """{app}\aMusicServer.exe"""; \
    Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\aMusicServer.exe"; Description: "Launch {#AppName} now"; \
    Flags: postinstall nowait skipifsilent

[UninstallRun]
; Best-effort: ask the running tray to quit before uninstall removes its files.
Filename: "taskkill.exe"; Parameters: "/IM aMusicServer.exe /F"; Flags: runhidden; \
    RunOnceId: "KillTray"

[Code]
function InitializeSetup(): Boolean;
begin
  // Heads-up if Chrome isn't installed — SC token refresh is optional.
  if not (FileExists(ExpandConstant('{commonpf}\Google\Chrome\Application\chrome.exe')) or
          FileExists(ExpandConstant('{commonpf32}\Google\Chrome\Application\chrome.exe')) or
          FileExists(ExpandConstant('{localappdata}\Google\Chrome\Application\chrome.exe'))) then
  begin
    MsgBox(
      'Google Chrome was not detected.' #13#10 #13#10 +
      'aMusicServer will still work without it — SoundCloud downloads use a ' +
      'built-in yt-dlp fallback. Chrome is only needed if you want automatic ' +
      'SoundCloud token refresh.' #13#10 #13#10 +
      'You can install Chrome later from https://www.google.com/chrome/',
      mbInformation, MB_OK
    );
  end;
  Result := True;
end;
