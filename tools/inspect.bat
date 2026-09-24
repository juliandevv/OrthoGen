@echo off
rem Launcher for the cross-band inspection tool — runs inspect_pair.py on the ODM venv
rem Python bundled with WebODM Desktop (has opencv/numpy/rasterio/GDAL).
rem Usage from the project root:  .\tools\inspect.bat --capture 0188 --band NIR --detector all
setlocal
set "ODX_VENV=C:\WebODM\resources\app\apps\ODX\venv\Scripts\python.exe"
"%ODX_VENV%" "%~dp0inspect_pair.py" %*
endlocal
