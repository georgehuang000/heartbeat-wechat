[Setup]
AppName=HeartWeChat prerequisite logic test
AppVersion=1
CreateAppDir=no
Uninstallable=no
PrivilegesRequired=lowest
OutputBaseFilename=webview2-prerequisite-test

[Code]
var
  Installed, InstalledAfterRun, CanLaunch: Boolean;
  SimulatedExit, Launches, Cases: Integer;
  LastError: String;

function HasWebView: Boolean;
begin
  Result := Installed;
end;

function RunBundledWebViewSetup(var ExitCode: Integer): Boolean;
begin
  Launches := Launches + 1;
  ExitCode := SimulatedExit;
  Result := CanLaunch;
  if Result then Installed := InstalledAfterRun;
end;

#include "..\webview2-prerequisite.iss"

procedure CheckCase(Name: String; InitiallyInstalled, AfterRun, LaunchOK: Boolean;
  Code, ExpectedLaunches: Integer; ExpectedSuccess, ExpectedRestart: Boolean);
var NeedsRestart: Boolean; ErrorText: String;
begin
  Installed := InitiallyInstalled;
  InstalledAfterRun := AfterRun;
  CanLaunch := LaunchOK;
  SimulatedExit := Code;
  Launches := 0;
  NeedsRestart := False;
  ErrorText := EnsureWebView2(NeedsRestart);
  LastError := ErrorText;
  if ((ErrorText = '') <> ExpectedSuccess) or (Launches <> ExpectedLaunches) or
     (NeedsRestart <> ExpectedRestart) then
    RaiseException('Failed prerequisite case: ' + Name);
  Cases := Cases + 1;
  Log('PASS: ' + Name);
end;

function InitializeSetup: Boolean;
begin
  CheckCase('existing runtime is never reinstalled', True, True, False, 0, 0, True, False);
  CheckCase('missing runtime installs successfully', False, True, True, 0, 1, True, False);
  CheckCase('zero exit without runtime stops setup', False, False, True, 0, 1, False, False);
  CheckCase('failed installer stops setup', False, False, True, -2147024809, 1, False, False);
  CheckCase('launch failure stops setup', False, False, False, 5, 1, False, False);
  CheckCase('concurrent install accepted after detection', False, True, True, -2147219416, 1, True, False);
  CheckCase('restart required stops until restart', False, False, True, 3010, 1, False, True);
  CheckCase('restart initiated is reported', False, True, True, 1641, 1, False, True);
#if WebViewMode == "standard"
  CheckCase('network timeout stops setup with offline fallback', False, False, True, -2147012894, 1, False, False);
  if (Pos('检查网络', LastError) = 0) or (Pos('完整离线包', LastError) = 0) then
    RaiseException('Network failure has no retry / offline guidance.');
  CheckCase('retry after network failure installs', False, True, True, 0, 1, True, False);
#endif
  if not SaveStringToFile(ExpandConstant('{src}\prerequisite-logic-check.json'),
    '{"passed":true,"cases":' + IntToStr(Cases) + ',"real_runtime_installed":false,"registry_changed":false}', False) then
    RaiseException('Could not save prerequisite test report.');
  { This test only executes the shared logic above; it never installs an app. }
  Result := False;
end;
