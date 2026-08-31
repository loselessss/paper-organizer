#define MyAppName "Paper Organizer"
#define MyAppVersion "2.4.0"
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

[Messages]
FinishedLabel=Paper Organizer 설치가 완료되었습니다.%n%n기본 설치에는 Vulkan·CPU 실행 기능이 포함됩니다. NVIDIA GPU용 CUDA는 필요할 때 앱의 AI 설정 → CUDA 선택 설치에서 약 642MB를 다운로드할 수 있습니다.%n%nAI가 필요 없으면 AI 설정에서 'AI 분석 없이 서지만 정리'를 선택하세요.

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

[Code]
var
  DeleteCredentialsCheck: TNewCheckBox;

procedure InitializeUninstallProgressForm;
begin
  DeleteCredentialsCheck := TNewCheckBox.Create(UninstallProgressForm);
  DeleteCredentialsCheck.Parent := UninstallProgressForm.InnerPage;
  DeleteCredentialsCheck.Left := 0;
  DeleteCredentialsCheck.Top := UninstallProgressForm.StatusLabel.Top + 40;
  DeleteCredentialsCheck.Width := UninstallProgressForm.InnerPage.ClientWidth;
  DeleteCredentialsCheck.Caption := 'Paper Organizer의 Windows 자격 증명(API 키)도 삭제';
  DeleteCredentialsCheck.Checked := False;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  if (CurUninstallStep = usUninstall) and DeleteCredentialsCheck.Checked then
    Exec(ExpandConstant('{app}\{#MyAppExeName}'), '--delete-credentials', '',
      SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;
