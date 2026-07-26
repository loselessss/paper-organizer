#define MyAppName "Paper Organizer"
#define MyAppVersion "1.3.0"
#define MyAppPublisher "SANGKYU SHIN, Ph.D."
#define MyAppExeName "PaperOrganizer.exe"

[Setup]
AppId={{8B5F8FB5-06C8-4C22-984F-F8471434A60E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\Paper Organizer
DefaultGroupName=Paper Organizer
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=Output
OutputBaseFilename=PaperOrganizer_Setup_{#MyAppVersion}
SetupIconFile=paper_organizer\assets\paper-organizer.ico
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "바탕 화면 바로가기 만들기"; GroupDescription: "추가 바로가기:"; Flags: unchecked
Name: "startup"; Description: "Windows 로그인 시 백그라운드로 시작"; GroupDescription: "자동 시작:"; Flags: unchecked

[Files]
Source: "dist\PaperOrganizer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Paper Organizer"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\Paper Organizer"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "PaperOrganizer"; ValueData: """{app}\{#MyAppExeName}"" --background"; Tasks: startup; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Paper Organizer 실행"; Flags: nowait postinstall skipifsilent
