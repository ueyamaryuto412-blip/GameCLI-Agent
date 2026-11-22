@echo off
setlocal
if exist "%~dp0.venv\Scripts\python.exe" set "PATH=%~dp0.venv\Scripts;%PATH%"
where codex >nul 2>nul || for /f "delims=" %%D in ('powershell.exe -NoProfile -Command "(Get-AppxPackage OpenAI.Codex).InstallLocation"') do set "PATH=%%D\app\resources;%PATH%"

start "GameCLI Backend" /D "%~dp0backend" cmd /k "python -m pip install -r requirements.txt && python -m uvicorn main:app --host 127.0.0.1 --port 8000"
start "GameCLI Frontend" /D "%~dp0frontend" cmd /k "npm.cmd ci --no-audit --no-fund && npm.cmd run dev"
echo Dashboard: http://localhost:5173
