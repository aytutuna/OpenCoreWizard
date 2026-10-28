@echo off
REM Python lookup and install based on https://github.com/corpnewt/SSDTTime/blob/97a3963e40a153a8df5ae61a73e150cd7a119b3c/SSDTTime.bat
REM No arguments: open the window without a console. Any arguments (--terminal, --headless ...): run in this console.
REM Get our local path before delayed expansion - allows ! in path
set "thisDir=%~dp0"

setlocal enableDelayedExpansion
set "script=!thisDir!OpenCoreWizard.py"
set /a tried=0
set "py3v="
set "py3path="

REM Get the system32 (or equivalent) path
call :getsyspath "syspath"

REM Make sure the syspath exists
if "!syspath!" == "" (
    if exist "%SYSTEMROOT%\system32\cmd.exe" (
        if exist "%SYSTEMROOT%\system32\reg.exe" (
            if exist "%SYSTEMROOT%\system32\where.exe" (
                REM Fall back on the default path if it exists
                set "ComSpec=%SYSTEMROOT%\system32\cmd.exe"
                set "syspath=%SYSTEMROOT%\system32\"
            )
        )
    )
    if "!syspath!" == "" (
        echo Could not locate cmd.exe, reg.exe, or where.exe
        echo.
        echo Please ensure your ComSpec environment variable is properly configured and
        echo points directly to cmd.exe, then try again.
        echo.
        echo Current ComSpec Value: "%ComSpec%"
        echo.
        echo Press [enter] to quit.
        pause > nul
        exit /b 1
    )
)

if not exist "!script!" (
    echo Could not find OpenCoreWizard.py next to this file.
    echo.
    echo Press [enter] to quit.
    pause > nul
    exit /b 1
)

:checkpy
call :updatepath
for /f "USEBACKQ tokens=*" %%x in (`!syspath!where.exe python 2^> nul`) do ( call :checkpyversion "%%x" )
for /f "USEBACKQ tokens=*" %%x in (`!syspath!where.exe python3 2^> nul`) do ( call :checkpyversion "%%x" )
for /f "USEBACKQ tokens=*" %%x in (`!syspath!where.exe py 2^> nul`) do ( call :checkpylauncher "%%x" )
if not "!py3path!" == "" goto runscript
if !tried! lss 1 goto askinstall
echo Python 3 is not installed or not found in your PATH var.
echo Please install it from https://www.python.org/downloads/windows/
echo and check the box labeled "Add Python X.X to PATH".
echo.
echo Press [enter] to quit.
pause > nul
exit /b 1

:runscript
if "%~1" == "" (
    for %%d in ("!py3path!") do set "pydir=%%~dpd"
    if exist "!pydir!pythonw.exe" (
        start "" "!pydir!pythonw.exe" "!script!"
        exit /b 0
    )
)
"!py3path!" "!script!" %*
set "code=!errorlevel!"
if not "!code!" == "0" (
    echo.
    echo OpenCore Wizard exited with error code: !code!
    echo.
    echo Press [enter] to exit...
    pause > nul
)
exit /b !code!

:checkpylauncher <path>
for /f "USEBACKQ tokens=*" %%x in (`"%~1" -3 -c "import sys; print(sys.executable)" 2^> nul`) do ( call :checkpyversion "%%x" )
goto :EOF

:checkpyversion <path>
set "version="&for /f "tokens=2* USEBACKQ delims= " %%a in (`"%~1" -V 2^>^&1`) do (
    REM Ensure we have a version number
    call :isnumber "%%a"
    if not "!errorlevel!" == "0" goto :EOF
    set "version=%%a"
)
if not defined version goto :EOF
if not "!version:~0,2!" == "3." goto :EOF
call :comparepyversion "!version!" "!py3v!"
if "!errorlevel!" == "1" (
    set "py3v=!version!"
    set "py3path=%~1"
)
goto :EOF

:isnumber <check_value>
set "var="&for /f "delims=0123456789." %%i in ("%~1") do set var=%%i
if defined var (exit /b 1)
exit /b 0

:comparepyversion <version1> <version2>
REM Exits with status 0 if equal, 1 if v1 gtr v2, 2 if v1 lss v2
for /f "tokens=1,2,3 delims=." %%a in ("%~1") do (
    set a1=%%a
    set a2=%%b
    set a3=%%c
)
for /f "tokens=1,2,3 delims=." %%a in ("%~2") do (
    set b1=%%a
    set b2=%%b
    set b3=%%c
)
if not defined a1 set a1=0
if not defined a2 set a2=0
if not defined a3 set a3=0
if not defined b1 set b1=0
if not defined b2 set b2=0
if not defined b3 set b3=0
if %a1% gtr %b1% exit /b 1
if %a1% lss %b1% exit /b 2
if %a2% gtr %b2% exit /b 1
if %a2% lss %b2% exit /b 2
if %a3% gtr %b3% exit /b 1
if %a3% lss %b3% exit /b 2
exit /b 0

:askinstall
echo Python 3 was not found on the system or in the PATH var.
echo.
set "menu="
set /p "menu=Would you like to install it now? [y/n]: "
if /i "!menu!"=="y" goto installpy
if /i "!menu!"=="n" (
    set /a tried=!tried!+1
    goto checkpy
)
REM No answer at all (stdin closed): give up instead of asking forever
if "!menu!"=="" (
    set /a tried=!tried!+1
    goto checkpy
)
goto askinstall

:installpy
REM Read the latest release from the python.org downloads page, then run its installer silently
set /a tried=!tried!+1
echo.
echo Gathering info from https://www.python.org/downloads/windows/...
powershell -command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12;(new-object System.Net.WebClient).DownloadFile('https://www.python.org/downloads/windows/','%TEMP%\pyurl.txt')"
REM Extract it if it's gzip compressed
powershell -command "$infile='%TEMP%\pyurl.txt';$outfile='%TEMP%\pyurl.temp';try{$input=New-Object System.IO.FileStream $infile,([IO.FileMode]::Open),([IO.FileAccess]::Read),([IO.FileShare]::Read);$output=New-Object System.IO.FileStream $outfile,([IO.FileMode]::Create),([IO.FileAccess]::Write),([IO.FileShare]::None);$gzipStream=New-Object System.IO.Compression.GzipStream $input,([IO.Compression.CompressionMode]::Decompress);$buffer=New-Object byte[](1024);while($true){$read=$gzipstream.Read($buffer,0,1024);if($read -le 0){break};$output.Write($buffer,0,$read)};$gzipStream.Close();$output.Close();$input.Close();Move-Item -Path $outfile -Destination $infile -Force}catch{}"
if not exist "%TEMP%\pyurl.txt" goto checkpy
echo Parsing for latest...
set "release="
pushd "%TEMP%"
:: Version detection code slimmed by LussacZheng (https://github.com/corpnewt/gibMacOS/issues/20)
for /f "tokens=9 delims=< " %%x in ('findstr /i /c:"Latest Python 3 Release" pyurl.txt') do ( set "release=%%x" )
popd
del "%TEMP%\pyurl.txt"
if "!release!" == "" goto checkpy
echo Found Python !release! - Downloading...
set "url=https://www.python.org/ftp/python/!release!/python-!release!-amd64.exe"
powershell -command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; (new-object System.Net.WebClient).DownloadFile('!url!','%TEMP%\pyinstall.exe')"
if not exist "%TEMP%\pyinstall.exe" goto checkpy
echo Installing...
pushd "%TEMP%"
echo pyinstall.exe /quiet PrependPath=1 Include_test=0 Shortcuts=0 Include_launcher=0
pyinstall.exe /quiet PrependPath=1 Include_test=0 Shortcuts=0 Include_launcher=0
popd
echo Installer finished with %ERRORLEVEL% status.
del "%TEMP%\pyinstall.exe"
REM The new PATH is not in this session yet, checkpy reads it back from the registry
goto checkpy

:undouble <string_name> <string_value> <character>
REM Helper function to strip doubles of a single character out of a string recursively
set "string_value=%~2"
:undouble_continue
set "check=!string_value:%~3%~3=%~3!"
if not "!check!" == "!string_value!" (
    set "string_value=!check!"
    goto :undouble_continue
)
set "%~1=!check!"
goto :EOF

:updatepath
set "spath="
set "upath="
for /f "USEBACKQ tokens=2* delims= " %%i in (`!syspath!reg.exe query "HKCU\Environment" /v "Path" 2^> nul`) do ( if not "%%j" == "" set "upath=%%j" )
for /f "USEBACKQ tokens=2* delims= " %%i in (`!syspath!reg.exe query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v "Path" 2^> nul`) do ( if not "%%j" == "" set "spath=%%j" )
if not "%spath%" == "" (
    REM We got something in the system path
    set "PATH=%spath%"
    if not "%upath%" == "" (
        REM We also have something in the user path
        set "PATH=%PATH%;%upath%"
    )
) else if not "%upath%" == "" (
    set "PATH=%upath%"
)
REM Remove double semicolons from the adjusted PATH
call :undouble "PATH" "%PATH%" ";"
goto :EOF

:getsyspath <variable_name>
REM Helper method to return a valid path to cmd.exe, reg.exe, and where.exe by
REM walking the ComSpec var - will also repair it in memory if need be
REM Strip double semi-colons
call :undouble "temppath" "%ComSpec%" ";"

REM Dirty hack to leverage the "line feed" approach - there are some odd side
REM effects with this.  Do not use this variable name in comments near this
REM line - as it seems to behave erradically.
(set LF=^
%=this line is empty=%
)
REM Replace instances of semi-colons with a line feed and wrap
REM in parenthesis to work around some strange batch behavior
set "testpath=%temppath:;=!LF!%"

REM Let's walk each path and test if cmd.exe, reg.exe, and where.exe exist there
set /a found=0
for /f "tokens=* delims=" %%i in ("!testpath!") do (
    REM Only continue if we haven't found it yet
    if not "%%i" == "" (
        if !found! lss 1 (
            set "checkpath=%%i"
            REM Remove "cmd.exe" from the end if it exists
            if /i "!checkpath:~-7!" == "cmd.exe" (
                set "checkpath=!checkpath:~0,-7!"
            )
            REM Pad the end with a backslash if needed
            if not "!checkpath:~-1!" == "\" (
                set "checkpath=!checkpath!\"
            )
            REM Let's see if cmd, reg, and where exist there - and set it if so
            if EXIST "!checkpath!cmd.exe" (
                if EXIST "!checkpath!reg.exe" (
                    if EXIST "!checkpath!where.exe" (
                        set /a found=1
                        set "ComSpec=!checkpath!cmd.exe"
                        set "%~1=!checkpath!"
                    )
                )
            )
        )
    )
)
goto :EOF
