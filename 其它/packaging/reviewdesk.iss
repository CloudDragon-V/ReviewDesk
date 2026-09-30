#define MyAppName "ReviewDesk"
#define MyAppVersion "0.1.0"
[Setup]
AppId={{552E6870-092B-4C84-9C49-17046ED6E7DA}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=ReviewDesk Project
DefaultDirName={localappdata}\Programs\ReviewDesk
DefaultGroupName=ReviewDesk
DisableProgramGroupPage=yes
OutputDir=..\安装包
OutputBaseFilename=ReviewDesk-0.1.0-Setup-win-x64
SetupIconFile=..\..\前端功能\resources\reviewdesk.ico
UninstallDisplayIcon={app}\ReviewDesk.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
CloseApplications=yes
LicenseFile=..\LICENSE

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "..\运行文件\ReviewDesk\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\ReviewDesk"; Filename: "{app}\ReviewDesk.exe"
Name: "{autodesktop}\ReviewDesk"; Filename: "{app}\ReviewDesk.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ReviewDesk.exe"; Description: "Launch ReviewDesk"; Flags: nowait postinstall skipifsilent
