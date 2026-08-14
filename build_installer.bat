@echo off
setlocal
cd /d "%~dp0"

call build_exe.bat
if errorlevel 1 exit /b %errorlevel%

set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if exist "%ISCC%" goto compile_installer
set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if exist "%ISCC%" goto compile_installer
for /f "delims=" %%I in ('where ISCC.exe 2^>nul') do (
  set "ISCC=%%I"
  goto compile_installer
)
echo [ERROR] Inno Setup 6 was not found.
exit /b 1

:compile_installer
"%ISCC%" installer.iss
if errorlevel 1 exit /b %errorlevel%

for /f "tokens=3" %%V in ('findstr /b /c:"#define MyAppVersion" installer.iss') do set "APP_VERSION=%%~V"
echo [OK] Output\PaperOrganizer_Setup_%APP_VERSION%.exe
endlocal
