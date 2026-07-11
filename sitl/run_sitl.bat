@echo off
REM Launch ArduPlane SITL (Software-In-The-Loop) flight simulator.
REM SERIAL0 is exposed on TCP port 5760 -- connect the GCS to tcp:127.0.0.1:5760
REM Home location is CMAC (the classic ArduPilot test field). Change -O to fly elsewhere.
REM Pass -w the first time (or after a param reset) to wipe the simulated EEPROM.

cd /d "%~dp0"
ArduPlane.exe -M plane -O -35.363261,149.165230,584,353 --speedup 1 %*
