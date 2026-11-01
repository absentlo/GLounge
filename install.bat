@echo off
rem Installs GLounge. Usage: install.bat ["REAPER resource folder"]
set "REAPER=%APPDATA%\REAPER"
if not "%~1"=="" set "REAPER=%~1"

if not exist "%REAPER%\Effects\" (
  echo REAPERs Effects folder was not found at: %REAPER%\Effects
  echo Open REAPER once, or pass its folder: install.bat "D:\REAPER"
  pause
  exit /b 1
)

if not exist "%REAPER%\Effects\GLounge\" mkdir "%REAPER%\Effects\GLounge"
copy /Y "%~dp0*.jsfx" "%REAPER%\Effects\GLounge\" >nul
if errorlevel 1 (
  echo Could not copy the plugin. Is the folder read-only?
  pause
  exit /b 1
)

echo Installed to %REAPER%\Effects\GLounge. Restart REAPER, or press F5 in the FX browser.
pause
