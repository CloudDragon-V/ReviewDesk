@echo off
chcp 65001 >nul
setlocal
set "REVIEWDESK_PYTHON=%~dp0..\其它\.venv\Scripts\python.exe"
if not exist "%REVIEWDESK_PYTHON%" (
  echo 找不到开发环境。请用 其它\安装包 中的 EXE 安装，或按 其它\README.md 创建环境。
  pause
  exit /b 1
)
"%REVIEWDESK_PYTHON%" "%~dp0app.py" %*
if errorlevel 1 pause
