@echo off
setlocal
cd /d "%~dp0"

call build_exe.bat
if errorlevel 1 exit /b %errorlevel%

set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if exist "%ISCC%" goto compile_installer
echo [ERROR] Inno Setup 6 was not found.
exit /b 1

:compile_installer
"%ISCC%" installer.iss
if errorlevel 1 exit /b %errorlevel%

echo [OK] Output\PaperOrganizer_Setup_1.1.0.exe
endlocal
