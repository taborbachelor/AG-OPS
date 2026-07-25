# Ag-Ops Ground Control Station

Autonomous field-spraying drone platform: a custom ground control station (operator side) +
customer ordering site (order → draw/detect field → schedule → pay → track), built on a
real MAVLink/ArduPlane stack. Runs against a bundled SITL simulator out of the box — no
hardware required to try it.

## Quick start (SITL, no hardware)

**First time only** — install dependencies:
```powershell
cd backend; python -m venv venv; .\venv\Scripts\Activate.ps1; pip install -r requirements.txt; cd ..
cd frontend; npm install; cd ..
cd web; npm install; cd ..
```

**Every time after** — one command from the repo root:
```powershell
.\start-all.ps1
```
This launches the SITL simulator, backend, GCS, and customer site each in their own window,
and opens the GCS in your browser. Give SITL ~30s to get a GPS fix, then in the GCS: click the
link chip (top-left) → **Quick Connect → Simulator**.

### Flagship demo (30 seconds)
SPRAY → Area → box some farmland near Sabetha, KS → **Detect fields in area** (USDA imagery
traces the real fields, crop-labeled) → Generate Spray Plan (spray passes + transits + home
legs) → Upload → FLY view → **ARM & TAKEOFF**.

### Manual startup (if you'd rather run pieces individually)
```powershell
# 1) SITL
sitl\run_sitl.bat

# 2) Backend (new window) — do NOT use --reload, see CLAUDE-CALEB.md "Dev loop"
cd backend; .\venv\Scripts\Activate.ps1; uvicorn app.main:app --host 0.0.0.0 --port 8000

# 3) GCS (new window)
cd frontend; npm start          # http://localhost:3000

# 4) Customer site (new window)
cd web; npm run dev             # http://localhost:3001
```

## Real hardware
Same flow, but in Quick Connect pick the detected COM port instead of Simulator (Cube over
USB, 115200 baud). See CLAUDE-CALEB.md for bench-testing notes and safety interlocks.

## Stack
- **Backend:** Python 3.13 / FastAPI / pymavlink / pyserial
- **GCS frontend:** React / Leaflet / Recharts
- **Customer site:** React / Vite / Leaflet
- **Communication:** MAVLink over serial/USB telemetry radio (or `tcp:127.0.0.1:5760` for SITL)

## API docs
Full endpoint list at `http://localhost:8000/docs` once the backend is running, or see
CLAUDE-CALEB.md for the annotated summary.

## Project docs
Architecture, decisions, session history, and the working task list live in
**CLAUDE-CALEB.md** — read that before making changes.
