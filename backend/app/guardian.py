"""GCS-side guardian: independent failsafe monitors + the emergency state
machine (ARCHITECTURE.md "Failsafe architecture").

Second layer of defense ABOVE ArduPilot's onboard failsafes — the onboard ones
stay primary (they work with the GCS link dead). The guardian:
  - watches what the autopilot doesn't (RTL energy margin vs distance home),
  - warns the operator earlier and by name,
  - can command RTL itself, ALWAYS recording the triggering condition first
    (directive: "never issue emergency commands without recording the
    triggering condition"),
  - never fights the operator: if the operator overrides a guardian RTL while
    the condition persists, the guardian logs it and stands down until the
    condition clears.

This module is PURE LOGIC — evaluate()/derive_state() have no side effects and
no locks, so every rule is unit-testable with plain dicts. The 1Hz runner
thread, config storage, and the actual RTL command live in vehicle_manager.

Design choices worth remembering:
  - Monitors only judge while ARMED: a bench vehicle with no GPS and a dead
    pack is normal, not an emergency.
  - GPS loss is warn-only by default and RTL is not offered for it: a plane
    without a position solution cannot navigate home; ArduPilot's own
    dead-reckoning/CIRCLE handling is the right reaction.
  - Battery RTL requires the condition to PERSIST (default 10s, mirroring
    ArduPilot's BATT_LOW_TIMER) so a throttle-up voltage sag can't trigger it.
"""
import math
from dataclasses import asdict, dataclass, field

# Emergency states (directive order). LANDING is entered via the land() flow;
# DISARMED is sticky after a flight until the next arm.
NORMAL = "NORMAL"
WARNING = "WARNING"
RTL_REQUESTED = "RTL_REQUESTED"
RTL_ACTIVE = "RTL_ACTIVE"
LANDING = "LANDING"
DISARMED = "DISARMED"

_LINK_LEVEL_ORDER = {"good": 0, "nominal": 1, "degraded": 2, "poor": 3,
                     "critical": 4}


@dataclass
class GuardianConfig:
    enabled: bool = True
    # GPS monitor (warn-only by default — see module docstring).
    gps_min_sats: int = 6
    gps_action: str = "warn"          # "warn" | "rtl"
    # Battery monitor — set these ABOVE the vehicle's own BATT_LOW_VOLT so the
    # guardian is the early layer and the autopilot the last-resort layer.
    batt_warn_volt: float = 10.8
    batt_rtl_volt: float = 10.4
    batt_action: str = "rtl"          # "warn" | "rtl"
    batt_low_s: float = 10.0          # sustained seconds below rtl_volt
    # Link monitor (vehicle-side FS_GCS is the actor; we inform the operator).
    link_warn_level: str = "poor"     # "degraded" | "poor" | "critical"
    # RTL energy margin. Needs the real pack capacity to estimate time
    # remaining; 0 disables the estimate (margin monitor reports unknown).
    pack_capacity_mah: float = 0.0
    margin_reserve_s: float = 90.0    # fixed reserve for approach + landing
    margin_action: str = "warn"       # "warn" | "rtl"
    margin_low_s: float = 10.0        # sustained seconds of negative margin
    # Assumed return speed when the plane is momentarily slow/turning (m/s).
    margin_min_speed: float = 12.0


def default_memory() -> dict:
    """Per-flight monitor memory (debounce timestamps + latches). Reset on arm."""
    return {"batt_low_since": None, "margin_low_since": None,
            "rtl_commanded_for": None, "standdown_for": None}


def _dist_home_m(t: dict) -> float | None:
    lat, lon = t.get("lat") or 0.0, t.get("lon") or 0.0
    hlat, hlon = t.get("home_lat") or 0.0, t.get("home_lon") or 0.0
    if not lat or not hlat:
        return None
    dn = (lat - hlat) * 111320.0
    de = (lon - hlon) * 111320.0 * math.cos(math.radians(hlat))
    return math.hypot(dn, de)


def evaluate(cfg: GuardianConfig, t: dict, mem: dict, now: float) -> dict:
    """Judge one telemetry snapshot.

    Returns {"monitors": {...}, "warnings": [str], "action": None|"rtl",
    "reason": str|None, "mem": mem} — mem is mutated in place with debounce
    state. The caller (runner thread) owns command execution and latching.
    """
    armed = bool(t.get("armed"))
    monitors: dict = {}
    warnings: list[str] = []
    action = None
    reason = None
    source = None  # which monitor demanded the action (latch/standdown key)

    # --- link ---
    level = t.get("link_level")
    link_warn = bool(
        armed and level is not None
        and _LINK_LEVEL_ORDER.get(level, 0) >= _LINK_LEVEL_ORDER[cfg.link_warn_level])
    monitors["link"] = {"ok": not link_warn, "level": level}
    if link_warn:
        warnings.append(f"link {level} (heartbeats thinning)")

    # --- gps ---
    fix, sats = t.get("gps_fix", 0), t.get("gps_satellites", 0)
    gps_bad = armed and fix < 3
    gps_thin = armed and not gps_bad and sats < cfg.gps_min_sats
    monitors["gps"] = {"ok": not (gps_bad or gps_thin), "fix": fix, "sats": sats}
    if gps_bad:
        warnings.append(f"GPS fix lost (fix={fix})")
        if cfg.gps_action == "rtl":
            action, reason, source = "rtl", f"GPS fix lost (fix={fix})", "gps"
    elif gps_thin:
        warnings.append(f"GPS thin ({sats} sats < {cfg.gps_min_sats})")

    # --- battery voltage ---
    volts = t.get("battery_voltage") or 0.0
    batt_warn = armed and 0 < volts < cfg.batt_warn_volt
    batt_low = armed and 0 < volts < cfg.batt_rtl_volt
    if batt_low:
        if mem["batt_low_since"] is None:
            mem["batt_low_since"] = now
    else:
        mem["batt_low_since"] = None
    batt_low_sustained = (mem["batt_low_since"] is not None
                          and now - mem["batt_low_since"] >= cfg.batt_low_s)
    monitors["battery"] = {"ok": not batt_warn, "volts": volts,
                           "low_sustained": batt_low_sustained}
    if batt_warn:
        warnings.append(f"battery {volts:.1f}V below warn {cfg.batt_warn_volt:.1f}V")
    if batt_low_sustained and cfg.batt_action == "rtl" and action is None:
        action, source = "rtl", "battery"
        reason = (f"battery {volts:.1f}V below RTL threshold "
                  f"{cfg.batt_rtl_volt:.1f}V for {cfg.batt_low_s:.0f}s")

    # --- RTL energy margin ---
    margin: dict = {"ok": True, "time_left_s": None, "time_home_s": None,
                    "margin_s": None}
    dist = _dist_home_m(t)
    current = t.get("battery_current") or 0.0
    consumed = t.get("battery_consumed_mah")
    if (armed and cfg.pack_capacity_mah > 0 and consumed is not None
            and current > 0.1 and dist is not None):
        remaining_mah = max(0.0, cfg.pack_capacity_mah - consumed)
        time_left = remaining_mah / (current * 1000.0) * 3600.0  # A -> mAh/h
        speed = max(t.get("groundspeed") or 0.0, cfg.margin_min_speed)
        time_home = dist / speed
        margin_s = time_left - time_home - cfg.margin_reserve_s
        margin = {"ok": margin_s >= 0, "time_left_s": round(time_left, 1),
                  "time_home_s": round(time_home, 1),
                  "margin_s": round(margin_s, 1)}
        if margin_s < 0:
            if mem["margin_low_since"] is None:
                mem["margin_low_since"] = now
            warnings.append(
                f"RTL margin negative ({margin_s:.0f}s): "
                f"{time_left:.0f}s of battery vs {time_home:.0f}s home + "
                f"{cfg.margin_reserve_s:.0f}s reserve")
            if (cfg.margin_action == "rtl" and action is None
                    and now - mem["margin_low_since"] >= cfg.margin_low_s):
                action, source = "rtl", "margin"
                reason = f"RTL energy margin negative for {cfg.margin_low_s:.0f}s"
        else:
            mem["margin_low_since"] = None
    else:
        mem["margin_low_since"] = None
    monitors["rtl_margin"] = margin

    if not cfg.enabled:
        action, reason, source = None, None, None

    return {"monitors": monitors, "warnings": warnings,
            "action": action, "reason": reason, "source": source, "mem": mem}


def derive_state(prev: str, armed: bool, mode: str, warnings: list,
                 rtl_commanded: bool, landing_requested: bool) -> str:
    """The emergency state machine, as a pure transition function.

    NORMAL -> WARNING -> RTL_REQUESTED -> RTL_ACTIVE -> LANDING -> DISARMED.
    RTL_ACTIVE is entered on the vehicle-truth mode, whoever commanded it
    (guardian, operator, or an onboard failsafe) — the state machine reports
    reality, not intent.
    """
    if not armed:
        # Sticky DISARMED after a flight; NORMAL before one.
        return DISARMED if prev not in (NORMAL,) else NORMAL
    if landing_requested and mode == "AUTO":
        return LANDING
    if mode == "RTL":
        return RTL_ACTIVE
    if rtl_commanded:
        return RTL_REQUESTED
    return WARNING if warnings else NORMAL


def config_to_dict(cfg: GuardianConfig) -> dict:
    return asdict(cfg)
