@echo off
echo Esta calibracao e SO para Joy-Con do Switch 1.
echo Joy-Con 2 do Switch 2 NAO precisa: ele calibra sozinho ao conectar no JoyconVR-Joycon2.exe.
echo.
echo Feche o SteamVR antes de calibrar.
pause
"%~dp0joyconvr\bin\win64\joycon_calibrate.exe"
