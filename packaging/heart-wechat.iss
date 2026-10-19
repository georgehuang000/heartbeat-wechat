#ifndef AppVersion
  #define AppVersion "0.1.1"
#endif
#define ProjectRoot SourcePath + ".."
#ifndef PayloadFile
  #define PayloadFile ProjectRoot + "\release\installer-payload\心动微信.exe"
#endif
#ifndef WebViewInstallerFile
  #error Use scripts/build_installer.ps1 to provide the verified WebView2 installer.
#endif
#ifndef WebViewMode
  #define WebViewMode "standard"
#endif
#if WebViewMode == "standard"
  #define WebViewInstallerName "MicrosoftEdgeWebview2Setup.exe"
#elif WebViewMode == "offline"
  #define WebViewInstallerName "MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
#else
  #error Unsupported WebViewMode.
#endif
#ifndef WebViewInstallerSha256
  #error The pinned Microsoft WebView2 SHA-256 is required.
#endif

[Setup]
AppId={{8D76F019-61CC-4993-A8E3-78C709614D0A}
AppName=心动微信
AppVersion={#AppVersion}
AppVerName=心动微信 {#AppVersion}
AppPublisher=心动微信
DefaultDirName={localappdata}\Programs\HeartWeChat
DefaultGroupName=心动微信
DisableProgramGroupPage=no
AllowNoIcons=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible and not arm64
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
OutputDir={#ProjectRoot}\release\installer
OutputBaseFilename=HeartWeChat-Setup-{#AppVersion}-x64
SetupIconFile={#ProjectRoot}\assets\heart-wechat.ico
UninstallDisplayIcon={app}\心动微信.exe
VersionInfoVersion={#AppVersion}.0
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableWelcomePage=no
#if WebViewMode == "offline"
InfoBeforeFile={#ProjectRoot}\packaging\install-notes-offline.txt
#else
InfoBeforeFile={#ProjectRoot}\packaging\install-notes.txt
#endif
CloseApplications=no
RestartApplications=no
SetupLogging=yes
UninstallLogging=yes
#ifdef SignRelease
SignTool=heartwechat
SignedUninstaller=yes
SignToolRetryCount=0
#else
SignedUninstaller=no
#endif

[Languages]
Name: "chinesesimplified"; MessagesFile: "vendor\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Files]
Source: "{#WebViewInstallerFile}"; DestName: "{#WebViewInstallerName}"; Hash: "{#WebViewInstallerSha256}"; Flags: dontcopy noencryption nocompression
Source: "{#PayloadFile}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProjectRoot}\packaging\quick-start.txt"; DestDir: "{app}"; DestName: "使用说明.txt"; Flags: ignoreversion
Source: "{#ProjectRoot}\packaging\THIRD-PARTY-NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\心动微信"; Filename: "{app}\心动微信.exe"; WorkingDir: "{app}"; Check: not WizardNoIcons
Name: "{autodesktop}\心动微信"; Filename: "{app}\心动微信.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\心动微信.exe"; Description: "启动心动微信"; Flags: nowait postinstall skipifsilent unchecked

[Code]
const
  WebViewKey = 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

procedure OpenWebViewDownload(Sender: TObject);
var ErrorCode: Integer;
begin
#if WebViewMode == "standard"
  ShellExec('open', 'https://github.com/georgehuang000/heartbeat-wechat/releases/tag/v{#AppVersion}',
#else
  ShellExec('open', 'https://developer.microsoft.com/microsoft-edge/webview2',
#endif
    '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;

procedure InitializeWizard;
var DownloadLink: TNewStaticText;
begin
  DownloadLink := TNewStaticText.Create(WizardForm);
  DownloadLink.Parent := WizardForm;
  DownloadLink.Left := ScaleX(12);
  DownloadLink.Top := WizardForm.CancelButton.Top + ScaleY(7);
#if WebViewMode == "standard"
  DownloadLink.Caption := '组件下载失败？获取完整离线包';
#else
  DownloadLink.Caption := 'WebView2 安装帮助（Microsoft）';
#endif
  DownloadLink.Font.Color := clBlue;
  DownloadLink.Cursor := crHand;
  DownloadLink.OnClick := @OpenWebViewDownload;
end;

function HasWebViewAt(RootKey: Integer): Boolean;
var Version: String;
begin
  Result := RegQueryStringValue(RootKey, WebViewKey, 'pv', Version) and
    (Version <> '') and (Version <> '0.0.0.0');
end;

function HasWebView: Boolean;
begin
  Result := HasWebViewAt(HKLM32) or HasWebViewAt(HKCU32) or HasWebViewAt(HKCU64);
end;

function HasDotNet48: Boolean;
var Release: Cardinal;
begin
  Result := RegQueryDWordValue(HKLM32,
    'Software\Microsoft\NET Framework Setup\NDP\v4\Full', 'Release', Release)
    and (Release >= 528040);
end;

function RunBundledWebViewSetup(var ExitCode: Integer): Boolean;
begin
  ExtractTemporaryFile('{#WebViewInstallerName}');
#if WebViewMode == "standard"
  WizardForm.PreparingLabel.Caption := '正在从 Microsoft 下载并安装界面运行组件，请保持联网并稍候…';
#else
  WizardForm.PreparingLabel.Caption := '正在安装内置的界面运行组件，无需另行下载，请稍候…';
#endif
  WizardForm.Refresh;
  Result := Exec(ExpandConstant('{tmp}\{#WebViewInstallerName}'), '/silent /install',
    '', SW_HIDE, ewWaitUntilTerminated, ExitCode);
end;

#include "webview2-prerequisite.iss"

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if not HasDotNet48 then begin
    Result := '需要 Microsoft .NET Framework 4.8。请先通过 Windows 更新安装，再重新运行安装包。';
    exit;
  end;
  Result := EnsureWebView2(NeedsRestart);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    MsgBox('程序已卸载。你的设置与本地记录保留在：' + #13#10 +
      ExpandConstant('{localappdata}\HeartWeChat'), mbInformation, MB_OK);
end;
