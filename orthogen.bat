@echo off
rem OrthoGen launcher — uses the ODM venv Python bundled with WebODM Desktop
rem (has numpy/rasterio/GDAL/opencv/pyproj/opendm). Usage: orthogen.bat <command> [args]
setlocal
set "ODX_VENV=C:\WebODM\resources\app\apps\ODX\venv\Scripts\python.exe"
"%ODX_VENV%" "%~dp0orthogen_cli.py" %*
endlocal
