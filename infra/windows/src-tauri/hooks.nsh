!define TRUSTREPRO_WEBVIEW_INSTALLER "${__FILEDIR__}\..\..\..\var\windows\MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
Var TrustReproPreviousBinary

!macro NSIS_HOOK_PREINSTALL
  ; Embed the verified Microsoft standalone installer directly. This avoids the
  ; bundler's unbounded large-file GET during build while preserving offline setup.
  ReadRegStr $2 HKLM "SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  ${If} $2 == ""
    ReadRegStr $2 HKCU "SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  ${EndIf}
  ${If} $2 == ""
    InitPluginsDir
    SetOutPath "$PLUGINSDIR"
    File /oname=MicrosoftEdgeWebView2RuntimeInstallerX64.exe "${TRUSTREPRO_WEBVIEW_INSTALLER}"
    ExecWait '"$PLUGINSDIR\MicrosoftEdgeWebView2RuntimeInstallerX64.exe" /silent /install' $2
    ${If} $2 != 0
    ${AndIf} $2 != 3010
      MessageBox MB_OK "浏览组件准备失败，请检查系统安装权限后重试。"
      SetErrorLevel 1
      Abort
    ${EndIf}
    SetOutPath "$INSTDIR"
  ${EndIf}
  IfFileExists "$INSTDIR\trustrepro-desktop.exe" 0 update_done
    nsExec::ExecToStack '"$INSTDIR\trustrepro-desktop.exe" --prepare-update'
    Pop $0
    Pop $1
    ${If} $0 != 0
      MessageBox MB_OK "任务仍在运行或无法确认状态。请完成或暂停任务后再更新。"
      SetErrorLevel 1
      Abort
    ${EndIf}
    ; Services have stopped durably. Close the GUI tree forcibly because its
    ; normal close action intentionally hides the window instead of exiting.
    nsExec::ExecToLog 'taskkill /IM trustrepro-desktop.exe /T /F'
    ; Keep the previous image in a unique file on the same volume. A lingering
    ; image/resource handle can still deny in-place writes after the GUI exits.
    IfFileExists "$INSTDIR\trustrepro-desktop.exe" 0 update_done
    ClearErrors
    GetTempFileName $TrustReproPreviousBinary "$INSTDIR"
    Delete "$TrustReproPreviousBinary"
    Rename "$INSTDIR\trustrepro-desktop.exe" "$TrustReproPreviousBinary"
    ${If} ${Errors}
      MessageBox MB_OK "无法替换正在使用的程序文件。请关闭 TrustRepro 相关窗口后重试，运行数据已保留。"
      SetErrorLevel 1
      Abort
    ${EndIf}
  update_done:
!macroend

!macro NSIS_HOOK_POSTINSTALL
  ${If} $TrustReproPreviousBinary != ""
    Delete "$TrustReproPreviousBinary"
  ${EndIf}
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  nsExec::ExecToStack '"$INSTDIR\trustrepro-desktop.exe" --prepare-uninstall'
  Pop $0
  Pop $1
  ${If} $0 != 0
    MessageBox MB_OK "无法确认任务已暂停。请打开应用，暂停任务并退出后重试。"
    SetErrorLevel 1
    Abort
  ${EndIf}
  nsExec::ExecToLog 'taskkill /IM trustrepro-desktop.exe /T /F'
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\RunOnce" "TrustReproResume"
  ; Deliberately keep the application-owned distro, database and encryption key.
!macroend
