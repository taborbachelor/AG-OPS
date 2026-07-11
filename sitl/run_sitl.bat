@echo off
REM Launch ArduPlane SITL (Software-In-The-Loop) flight simulator.
REM SERIAL0 is exposed on TCP port 5760 -- connect the GCS to tcp:127.0.0.1:5760
REM Home location is Sabetha, KS. Change -O (lat,lon,alt_m,heading) to fly elsewhere.
REM Pass -w the first time (or after a param reset) to wipe the simulated EEPROM.

cd /d "%~dp0"
ArduPlane.exe -M plane -O 39.9042,-95.7997,408,0 --speedup 1 %*
