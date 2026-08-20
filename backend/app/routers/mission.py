from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator
from typing import Literal
from app.vehicle_manager import vehicle_manager
from app.terrain_store import TerrainCoverageError, TerrainDataError, store

router = APIRouter()

CommandType = Literal["TAKEOFF", "WAYPOINT", "LOITER", "LAND", "RTL"]
FrameType = Literal["relative", "terrain"]

# Terrain framing only makes sense for items that fly TO somewhere at an
# altitude. A takeoff climbs relative to where it left the ground and a landing
# ends at the ground, so a terrain frame on either is a misunderstanding rather
# than a preference — and RTL carries no position at all.
_TERRAIN_CAPABLE = {"WAYPOINT", "LOITER"}


class MissionItem(BaseModel):
    """Bounds matter beyond sanity: mission_item_int packs lat/lon as
    int32 * 1e7 — an out-of-range value would raise a struct error mid-
    transfer, aborting the upload with the vehicle left mid-transaction."""
    command: CommandType = "WAYPOINT"
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    alt: float = Field(ge=-500, le=10000)          # m relative; generous sanity
    param1: float = Field(0.0, ge=-100000, le=100000)
    # Loiter radius in meters (param3 for LOITER items; negative = CCW).
    radius: float = Field(0.0, ge=-100000, le=100000)
    # "relative" = alt above home (the old, only behaviour). "terrain" = alt
    # above the ground beneath the aircraft, which is what a spray pass means by
    # 20 m. Settles seam S3; see the LANES decisions log.
    frame: FrameType = "relative"

    @model_validator(mode="after")
    def _terrain_frame_only_where_it_means_something(self):
        if self.frame == "terrain" and self.command not in _TERRAIN_CAPABLE:
            raise ValueError(
                "%s cannot use the terrain frame — takeoff is relative to the "
                "launch point and landing ends at the ground. Use frame "
                "'relative' for %s items." % (self.command, self.command))
        return self


class MissionUpload(BaseModel):
    items: list[MissionItem]


# NOTE: plain `def` handlers run in FastAPI's threadpool so long mission
# transfers never freeze the event loop (RTL/disarm/telemetry stay live).


@router.get("/download")
def download_mission():
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    items = vehicle_manager.download_mission()
    return {"items": items}


def _check_terrain_ready(items):
    """Refuse a terrain-following mission we cannot actually support.

    This is where the bundling decision (LANES, 2026-08-19) is cashed in. A
    terrain-framed mission asks the aircraft to hold height above ground it
    learns about only from us, so the moment to discover we have no tiles for
    that field is here — on the ground, with a message naming the tile — and
    not at 20 m over it. Both checks refuse the upload outright rather than
    quietly demoting the mission to above-home framing, because a spray pass
    that silently changed what its altitude means is the exact failure the
    decision was written to prevent.
    """
    terrain_items = [i for i in items if i.get("frame") == "terrain"]
    if not terrain_items:
        return

    try:
        tiles = store()
    except TerrainDataError as exc:
        raise HTTPException(503, "Terrain-following mission refused: %s" % exc)

    try:
        tiles.require_coverage([(i["lat"], i["lon"]) for i in terrain_items],
                               "this terrain-following mission")
    except TerrainCoverageError as exc:
        raise HTTPException(400, str(exc))

    # The vehicle has to be willing to use it. TERRAIN_ENABLE=0 makes ArduPilot
    # ignore terrain entirely, so a TERRAIN_ALT item would be flown against a
    # model it never loaded.
    enabled = vehicle_manager.cached_value("TERRAIN_ENABLE")
    if enabled is None:
        enabled = vehicle_manager.get_param("TERRAIN_ENABLE")
    if enabled is None:
        raise HTTPException(
            503, "Terrain-following mission refused: could not read "
                 "TERRAIN_ENABLE from the vehicle, so there is no way to tell "
                 "whether it would honour the terrain frame.")
    if int(enabled) != 1:
        raise HTTPException(
            400, "Terrain-following mission refused: the vehicle has "
                 "TERRAIN_ENABLE=%g. Set TERRAIN_ENABLE=1 and TERRAIN_SPACING=%u "
                 "to match the bundled tiles." % (enabled, tiles.spacing))


@router.post("/upload")
def upload_mission(mission: MissionUpload):
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    items = [i.model_dump() for i in mission.items]
    _check_terrain_ready(items)
    result = vehicle_manager.upload_mission(items)
    if not result.get("ok"):
        raise HTTPException(500, f"Mission upload failed: {result}")
    # A new mission means the cached keepout rings describe the PREVIOUS
    # field. Stale rings are worse than none — they read as a confident
    # all-clear over ground nobody surveyed — so the live proximity monitor
    # goes back to "unknown" until the client re-arms it via
    # POST /api/safety/keepouts.
    vehicle_manager.clear_mission_keepouts(reason="mission_upload")
    return result


@router.get("/terrain")
def terrain_readiness(lat: float | None = None, lon: float | None = None):
    """Can we fly terrain-following here, and does the AIRCRAFT agree?

    Two different facts, deliberately reported separately. `bundle` is what the
    GCS has on disk; `vehicle` is what the aircraft says it has actually loaded,
    which is the one that decides whether it will arm. Passing lat/lon also
    pokes the aircraft with a TERRAIN_CHECK so its next report describes the
    field you are about to fly rather than wherever it last looked.
    """
    out = {"bundle": None, "vehicle": None, "error": None}
    try:
        tiles = store()
        out["bundle"] = {"tiles": tiles.tile_names(),
                         "grid_spacing_m": tiles.spacing,
                         "covers_point": (tiles.covers(lat, lon)
                                          if lat is not None and lon is not None
                                          else None)}
    except TerrainDataError as exc:
        out["error"] = str(exc)

    if vehicle_manager.connected:
        if lat is not None and lon is not None:
            vehicle_manager.request_terrain_report(lat, lon)
        snap = vehicle_manager.snapshot()
        out["vehicle"] = {
            # spacing 0 is ArduPilot's "no data here" — not sea level.
            "has_data": bool(snap.get("terrain_spacing")),
            "spacing": snap.get("terrain_spacing"),
            "terrain_height": snap.get("terrain_height"),
            "current_height": snap.get("terrain_current_height"),
            "pending": snap.get("terrain_pending"),
            "loaded": snap.get("terrain_loaded"),
            "served": snap.get("terrain"),
        }
    return out


@router.post("/start")
def start_mission():
    if not vehicle_manager.connected:
        raise HTTPException(400, "Not connected")
    # Ack-checked: never report "mission started" if the vehicle refused AUTO.
    if not vehicle_manager.set_mode("AUTO"):
        raise HTTPException(400, "Vehicle rejected switch to AUTO — mission NOT started")
    return {"status": "mission started"}
