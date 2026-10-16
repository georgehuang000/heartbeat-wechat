#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#define ProjectRoot SourcePath + ".."
#ifndef PayloadFile
  #define PayloadFile ProjectRoot + "\release\installer-payload\心动微信.exe"
#endif
#if FileExists(ProjectRoot + "\build\installer-prerequisites\MicrosoftEdgeWebview2Setup.exe")
  #define BundleWebView
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
InfoBeforeFile={#ProjectRoot}\packaging\install-notes.txt
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
Source: "{#PayloadFile}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProjectRoot}\packaging\quick-start.txt"; DestDir: "{app}"; DestName: "使用说明.txt"; Flags: ignoreversion
Source: "{#ProjectRoot}\packaging\THIRD-PARTY-NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion
#ifdef BundleWebView
Source: "{#ProjectRoot}\build\installer-prerequisites\MicrosoftEdgeWebview2Setup.exe"; Flags: dontcopy
#endif

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
  ShellExec('open', 'https://developer.microsoft.com/microsoft-edge/webview2',
    '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;

procedure InitializeWizard;
var DownloadLink: TNewStaticText;
begin
  DownloadLink := TNewStaticText.Create(WizardForm);
  DownloadLink.Parent := WizardForm;
  DownloadLink.Left := ScaleX(12);
  DownloadLink.Top := WizardForm.CancelButton.Top + ScaleY(7);
  DownloadLink.Caption := '下载 WebView2（Microsoft 官方）';
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

function PrepareToInstall(var NeedsRestart: Boolean): String;
var ExitCode: Integer;
begin
  Result := '';
  if not HasDotNet48 then begin
    Result := '需要 Microsoft .NET Framework 4.8。请先通过 Windows 更新安装，再重新运行安装包。';
    exit;
  end;
  if not HasWebView then begin
#ifdef BundleWebView
    ExtractTemporaryFile('MicrosoftEdgeWebview2Setup.exe');
    WizardForm.StatusLabel.Caption := '正在安装 Microsoft Edge WebView2 Runtime，需要联网…';
    if not Exec(ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe'), '/silent /install',
        '', SW_HIDE, ewWaitUntilTerminated, ExitCode) then begin
      Result := '无法启动 WebView2 安装程序。请从 Microsoft 官方页面安装 WebView2 Runtime 后重试。';
      exit;
    end;
    if not HasWebView then
      Result := 'WebView2 Runtime 尚未安装成功（退出码 ' + IntToStr(ExitCode) + '）。请检查网络，或从 Microsoft 官方页面安装后重试。';
#else
    Result := '需要 Microsoft Edge WebView2 Runtime。请点击窗口左下方的 Microsoft 官方下载入口，安装 Evergreen Runtime 后重新运行本安装包。当前软件安装包不含 WebView2 Runtime，安装程序不会在缺少依赖时继续。';
#endif
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    MsgBox('程序已卸载。你的设置与本地记录保留在：' + #13#10 +
      ExpandConstant('{localappdata}\HeartWeChat'), mbInformation, MB_OK);
end;
