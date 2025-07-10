@echo off
setlocal EnableDelayedExpansion

:: === Project Base Directory ===
set "BASE_DIR=%~dp0"
cd /d "%BASE_DIR%"

:: === Compose File Name ===
set "COMPOSE_FILE=docker-compose.yml"

:: === Welcome Menu ===
echo.
echo ===================================
echo  QUANTALGO - MASTER CONTROL PANEL
echo ===================================
echo 1. Run App (Docker)
echo 2. Run App (Local Python)
echo 3. Stop Docker Containers
echo 4. VS Code DevContainer
echo 5. Backup to OneDrive
echo 6. Restore from OneDrive
echo 0. Exit
echo ===================================
echo.

set /p CHOICE=Choose an option (0-6): 

if "%CHOICE%"=="1" goto docker_run
if "%CHOICE%"=="2" goto local_run
if "%CHOICE%"=="3" goto docker_stop
if "%CHOICE%"=="4" goto devcontainer
if "%CHOICE%"=="5" goto backup
if "%CHOICE%"=="6" goto restore
if "%CHOICE%"=="0" exit

goto:eof

:: -----------------------
:docker_run
echo [⚙] Running app in Docker mode...
docker compose -f "%BASE_DIR%%COMPOSE_FILE%" up --build -d
goto end

:: -----------------------
:local_run
echo [⚙] Running app in Local Python mode...
set "ENV_FILE=%BASE_DIR%.env"
if not exist "%ENV_FILE%" (
    echo ❌ ERROR: .env file not found at %ENV_FILE%
    pause
    goto end
)
call "%USERPROFILE%\.pyenv\pyenv-win\versions\3.11.9\python.exe" "%BASE_DIR%mcx.py"
goto end

:: -----------------------
:docker_stop
echo [🛑] Stopping Docker containers...
docker compose -f "%BASE_DIR%%COMPOSE_FILE%" down
goto end

:: -----------------------
:devcontainer
echo [🚀] Opening VS Code DevContainer...
code "%BASE_DIR%"
goto end

:: -----------------------
:backup
echo [💾] Running backup to OneDrive...
powershell -ExecutionPolicy Bypass -File "%BASE_DIR%backup_to_onedrive.ps1"
goto end

:: -----------------------
:restore
echo [♻] Restoring from OneDrive...
call "%BASE_DIR%restore_from_onedrive.bat"
goto end

:: -----------------------
:end
echo.
echo ✅ Done.
pause
