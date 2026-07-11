# SITL — Software-In-The-Loop Simulator

Runs a full ArduPilot flight controller in simulation so we can develop and test
the GCS without any hardware. It simulates real flight dynamics, GPS, battery,
attitude, and responds to arm/mode/mission commands — everything the UI needs.

## First-time setup

The binaries are large (~21 MB) and are **not** committed to git. Fetch them:

```bash
cd sitl
./download_sitl.sh
```

## Running

```bash
# From the sitl/ folder:
./run_sitl.bat
```

SITL boots and exposes the flight controller on **TCP port 5760**. In the GCS
connection dialog, connect to:

```
tcp:127.0.0.1:5760
```

Give it ~30 seconds after boot for GPS/EKF to converge (you'll see the sat count
climb and GPS switch to a 3D fix, and the plane appear on the map at the home
location).

## Notes

- **SITL exits when the GCS disconnects.** This is expected for this build — just
  re-run `run_sitl.bat`. As long as the GCS stays connected, it keeps flying.
- Home is set to Sabetha, KS (`39.9042,-95.7997`). Change the
  `-O lat,lon,alt,heading` argument in `run_sitl.bat` to start somewhere else.
- Add `-w` to wipe the simulated EEPROM back to default parameters.
- Other vehicle types are available from the same server (ArduCopter.elf,
  ArduRover.elf, etc.) if we ever simulate something other than a plane.
