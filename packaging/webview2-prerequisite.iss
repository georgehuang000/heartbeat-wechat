{ Shared production logic. HasWebView and RunBundledWebViewSetup are platform adapters. }
#ifndef WebViewMode
  #define WebViewMode "standard"
#endif
function EnsureWebView2(var NeedsRestart: Boolean): String;
var ExitCode: Integer;
begin
  Result := '';
  if HasWebView then begin
    Log('WebView2: existing Runtime detected; bundled installer skipped.');
    exit;
  end;
#if WebViewMode == "standard"
  Log('WebView2: running bundled Evergreen Bootstrapper; Runtime downloads directly from Microsoft.');
#else
  Log('WebView2: installing the bundled Evergreen Standalone Runtime.');
#endif
  if not RunBundledWebViewSetup(ExitCode) then begin
    Log('WebView2: could not launch bundled installer; error ' + IntToStr(ExitCode));
    Result := '无法启动内置 WebView2 安装程序（错误码 ' + IntToStr(ExitCode) + '）。请重新运行安装包；如仍失败，请查看安装日志。';
    exit;
  end;
  Log('WebView2: installer exit code ' + IntToStr(ExitCode));
  if (ExitCode = 3010) or (ExitCode = 1641) then begin
    NeedsRestart := True;
    Result := 'WebView2 安装要求重启。请保存工作并重启 Windows，然后重新运行心动微信安装包。';
    exit;
  end;
  { Evergreen can return an already-installed code if another installer raced us.
    The registered Runtime, not just a zero exit code, decides whether to continue. }
  if not HasWebView then begin
#if WebViewMode == "standard"
    Result := '界面运行组件未能下载或安装（退出码 ' + IntToStr(ExitCode) + '）。请检查网络后重试；也可以点击窗口左下方入口，下载完整离线包后安装。安装日志中包含错误码。';
#else
    Result := 'WebView2 Runtime 未安装成功（退出码 ' + IntToStr(ExitCode) + '）。请重试并查看安装日志；必要时可通过窗口左下方的 Microsoft 官方帮助入口修复。';
#endif
  end else
    Log('WebView2: Runtime registration verified after installation.');
end;
