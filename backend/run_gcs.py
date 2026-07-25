"""Single-exe entry point for the Ag-Ops GCS.

PyInstaller bundles this + the FastAPI backend + the built React frontend into
one AgOpsGCS.exe. Double-click -> server starts on :8000 -> browser opens.
Ship a sitl/ folder next to the exe and the Simulator button works too.
"""
import threading
import time
import webbrowser

import uvicorn

from app.main import app


def _open_browser():
    time.sleep(1.5)
    webbrowser.open("http://localhost:8000")


if __name__ == "__main__":
    threading.Thread(target=_open_browser, daemon=True).start()
    # No --reload in the exe (matches the dev-loop rule); log to console so a
    # terminal launch shows what's happening.
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
