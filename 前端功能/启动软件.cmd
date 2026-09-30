@echo off
chcp 65001 >nul
setlocal
set "REVIEWDESK_PYTHON=%~dp0..\其它\.venv\Scripts\pythonw.exe"
if not exist "%REVIEWDESK_PYTHON%" (
  echo 找不到开发环境。请双击 其它\安装包\ReviewDeskReading-0.2.0-Setup-win-x64.exe 安装。
  pause
  exit /b 1
)
start "" "%REVIEWDESK_PYTHON%" "%~dp0workbench.py" %*
