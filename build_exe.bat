@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv Python was not found.
  exit /b 1
)

".venv\Scripts\python.exe" scripts\prepare_llama_runtime.py --smoke
if errorlevel 1 exit /b %errorlevel%

".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean paper-organizer.spec
if errorlevel 1 exit /b %errorlevel%

".venv\Scripts\python.exe" scripts\prepare_llama_runtime.py --verify "dist\PaperOrganizer\_internal\llm" --smoke
if errorlevel 1 exit /b %errorlevel%

if not exist "dist\PaperOrganizer-ocr\spdf-ocr.exe" (
  echo [ERROR] The isolated sPDF OCR worker was not built.
  exit /b 1
)

if not exist "dist\PaperOrganizer\ocr" mkdir "dist\PaperOrganizer\ocr"
xcopy /E /I /Y "dist\PaperOrganizer-ocr\*" "dist\PaperOrganizer\ocr\" >nul
if errorlevel 1 exit /b %errorlevel%

echo [OK] dist\PaperOrganizer\PaperOrganizer.exe
endlocal
