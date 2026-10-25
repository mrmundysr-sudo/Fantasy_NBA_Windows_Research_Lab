; Optional per-user installer (Inno Setup 6). Build after PyInstaller: iscc packaging\inno_setup.iss
[Setup]
AppName=NBA Research Lab
AppVersion=0.1.0
DefaultDirName={localappdata}\Programs\NBALab
DefaultGroupName=NBA Research Lab
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=NBALab-Setup
Compression=lzma2
SolidCompression=yes
[Files]
Source: "..\dist\NBALab\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
[Icons]
Name: "{group}\NBA Research Lab"; Filename: "{app}\NBALab.exe"
Name: "{userdesktop}\NBA Research Lab"; Filename: "{app}\NBALab.exe"; Tasks: desktopicon
[Tasks]
Name: "desktopicon"; Description: "Desktop shortcut"
[Run]
Filename: "{app}\NBALab.exe"; Description: "Launch NBA Research Lab"; Flags: postinstall nowait skipifsilent
