# RC Plane Ground Control Station

Custom ground control station for 3D-printed RC plane/drone.

## Stack
- **Backend:** Python / FastAPI / pymavlink / dronekit
- **Frontend:** React (TBD - needs Node.js)
- **Communication:** MAVLink protocol over serial/USB telemetry radio

## Setup

### Backend
```bash
cd backend
python -m venv venv
source venv/Scripts/activate  # Windows
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### Frontend
```bash
cd frontend
npm install
npm start
```

## API Endpoints
- `GET /api/health` — health check
- `GET /api/connection/ports` — list serial ports
- `POST /api/connection/connect` — connect to vehicle
- `POST /api/connection/disconnect` — disconnect
- `GET /api/telemetry/` — current telemetry snapshot
- `WS /api/telemetry/ws` — real-time telemetry stream (10Hz)
- `GET /api/mission/download` — download mission from vehicle
- `POST /api/mission/upload` — upload waypoints
- `POST /api/vehicle/arm` — arm vehicle
- `POST /api/vehicle/disarm` — disarm vehicle
- `POST /api/vehicle/mode` — set flight mode
- `GET /api/vehicle/params` — get all parameters
