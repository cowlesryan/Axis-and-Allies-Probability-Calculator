; Inno Setup script. Build with build.ps1 (it passes the paths and version).
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef SourceDir
  #define SourceDir "dist\AAOdds"
#endif
#ifndef OutDir
  #define OutDir "installer"
#endif

[Setup]
AppId={{6C1B7E52-3D0A-4B7B-9C55-2A1F1E0A9D41}
AppName=A&A Odds Overlay
AppVersion={#AppVersion}
AppPublisher=Private build
DefaultDirName={localappdata}\Programs\AAOdds
DefaultGroupName=A&A Odds Overlay
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir={#OutDir}
OutputBaseFilename=AAOdds-{#AppVersion}-setup
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
UninstallDisplayName=A&A Odds Overlay
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
Name: "autostart"; Description: "Start with Windows"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\A&A Odds Overlay"; Filename: "{app}\AAOdds.exe"
Name: "{group}\Uninstall A&A Odds Overlay"; Filename: "{uninstallexe}"
Name: "{autodesktop}\A&A Odds Overlay"; Filename: "{app}\AAOdds.exe"; Tasks: desktopicon
Name: "{userstartup}\A&A Odds Overlay"; Filename: "{app}\AAOdds.exe"; Tasks: autostart

[Run]
Filename: "{app}\AAOdds.exe"; Description: "Start the overlay now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; The user's taught templates, settings and logs in %LOCALAPPDATA%\aaoverlay are kept on purpose.
Type: filesandordirs; Name: "{app}"
