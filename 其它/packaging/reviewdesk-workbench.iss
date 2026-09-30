#define MyAppName "ReviewDesk Reading"
#define MyAppVersion "0.2.0"
[Setup]
AppId={{A3C99F8B-48EE-4C35-8C32-5F773401F8E7}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=ReviewDesk Project
DefaultDirName={localappdata}\Programs\ReviewDeskReading
DefaultGroupName=ReviewDesk Reading
DisableProgramGroupPage=yes
AllowNoIcons=yes
OutputDir=..\安装包
OutputBaseFilename=ReviewDeskReading-0.2.0-Setup-win-x64
SetupIconFile=..\..\前端功能\resources\reviewdesk.ico
UninstallDisplayIcon={app}\ReviewDeskReading.exe
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
Source: "..\运行文件\ReviewDeskReading\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\ReviewDesk Reading"; Filename: "{app}\ReviewDeskReading.exe"
Name: "{autodesktop}\ReviewDesk Reading"; Filename: "{app}\ReviewDeskReading.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ReviewDeskReading.exe"; Description: "Launch ReviewDesk Reading"; Flags: nowait postinstall skipifsilent
