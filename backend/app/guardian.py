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

LOAD-BEARING ASSUMPTION — "KANSAS IS FLAT" (SPRAY-FLIGHT-SAFETY.md #8):
every monitor here treats altitude as height above the LAUNCH point, because
that is what `GLOBAL_POSITION_INT.relative_alt` gives us. There is no terrain
model in this stack. So `airborne_alt_m`, the bank monitor's low-altitude
tightening, and the RTL energy margin are all only as true as the ground being
level with home. Over the flat Kansas fields this is being built for that holds;
fly it into rolling ground and "15 m AGL" may be 15 m above the launch field
while the aircraft is much closer to — or below — a rise ahead of it. Terrain
awareness is explicitly a later phase (needs a camera + companion computer).
Until then this assumption is stated, not silently relied on.

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
    # EKF monitor. Binary "unhealthy" mirrors preflight's ekf check but watches
    # it continuously in flight (preflight only judges once, before arm).
    # Warn-only by default for the same reason GPS is: an EKF that's already
    # unhealthy means the position solution itself may not be trustworthy, so
    # autonomously commanding RTL could send the vehicle the wrong way — the
    # operator should decide, with whatever other cues (visual, RC) they have.
    ekf_action: str = "warn"          # "warn" | "rtl"
    # Variance leading indicators (EKF_STATUS_REPORT pos_horiz_variance /
    # velocity_variance): 0.0 is perfect, rising toward ArduPilot's own
    # internal EKF reject threshold (~1.0, see EK3_ERR_THRESH). A warning here
    # means "drifting", ahead of the flags actually flipping unhealthy —
    # always warn-only, it's an early heads-up, not itself a trigger.
    ekf_var_warn: float = 0.6
    # Vibration monitor (VIBRATION message). ArduPilot's own guidance treats
    # RMS vibration above ~30 m/s/s in any axis as high. This is the standard
    # early indicator of a developing mechanical problem (loose motor mount,
    # prop imbalance, bearing wear) — not something RTL fixes by itself, but
    # sustained high vibration or actively growing accelerometer clipping
    # means the airframe may be degrading mid-flight, worth escalating past a
    # threshold rather than only finding out after landing.
    vibe_warn_ms2: float = 30.0
    vibe_action: str = "warn"         # "warn" | "rtl"
    vibe_sustained_s: float = 5.0     # debounce, same shape as battery
    vibe_clip_warn: int = 5           # new clip events (since arm) before warning
    # Stall/airspeed margin. Airspeed already flows into telemetry (VFR_HUD);
    # this compares it against an operator-set safe-flight floor rather than
    # reading the vehicle's own ARSPD_FBW_MIN/AIRSPEED_MIN param (name varies
    # by firmware version, and guardian deliberately stays param-cache-free —
    # same "operator states the number" precedent as margin_min_speed /
    # pack_capacity_mah above). ArduPlane's own TECS/stall protection is the
    # primary defense here; this is the second, earlier layer that tells the
    # operator by name, same role the guardian plays everywhere else. Gated
    # on being airborne (altitude past a small threshold), not just armed —
    # airspeed is legitimately near zero while taxiing or mid-takeoff-roll,
    # and that must never read as a stall warning.
    airspeed_warn_ms: float = 10.0
    airspeed_min_ms: float = 8.0      # sustained below this is stall-risk territory
    airspeed_action: str = "warn"     # "warn" | "rtl"
    airspeed_low_s: float = 3.0       # shorter than battery — stall risk develops fast
    airborne_alt_m: float = 5.0       # below this, treat as ground ops, not flight
    # Bank angle. A steep bank raises stall speed (stall_speed / sqrt(cos(bank))
    # — 45 deg costs ~19%) while cutting the vertical lift component, and a
    # stall-spin entered in a spray turn has no altitude to recover in. The
    # real mitigation is planning-time turn geometry in coverage.py; this is
    # the in-flight sanity check that catches what the plan didn't command —
    # a gust, control saturation, or an operator stick input.
    #
    # 45 deg default = ArduPlane's own ROLL_LIMIT_DEG default. The threshold is
    # deliberately pinned to the autopilot's limit rather than picked by feel:
    # past it, the aircraft is banking harder than anything the autopilot
    # should be commanding, which is the gust / saturation / stick-input case
    # this monitor exists to catch.
    #
    # MEASURED 2026-08-18, and worth knowing before tuning this: this airframe
    # in SITL peaks at 50-65 deg of roll during ORDINARY loiter and RTL turns —
    # past ROLL_LIMIT_DEG. So this monitor does fire on routine turns today.
    # That is a true finding, not noise: a 60 deg bank raises stall speed ~41%
    # and at a 10-25 m spray altitude there is no room to recover a stall-spin.
    # The fix is the planning-time turn-geometry constraint
    # (SPRAY-FLIGHT-SAFETY.md #4) — this monitor is what makes the problem
    # visible until that lands. Operators flying low should set this DOWN.
    bank_warn_deg: float = 45.0
    bank_action: str = "warn"         # "warn" | "rtl"
    bank_sustained_s: float = 2.0     # a momentary gust-induced bank is not news
    # Low-altitude multiplier: below this height the same bank is far more
    # dangerous, so the monitor tightens automatically rather than relying on
    # the operator to remember. 0 disables the tightening.
    bank_low_alt_m: float = 30.0
    bank_low_alt_factor: float = 0.7  # 45 deg -> 31.5 deg below 30 m
    # Live keepout proximity. The planner already routes around hazards, so
    # this fires only when the aircraft is NOT where the plan put it — wind,
    # a mode change, an operator input, or a plan flown against stale zones.
    # Warn-only by default: an RTL that turns the aircraft toward home could
    # steer it ACROSS the very line it is close to, so the operator decides.
    keepout_action: str = "warn"      # "warn" | "rtl"
    keepout_sustained_s: float = 0.0  # 0 = warn immediately; a wire is not a debounce


def default_memory() -> dict:
    """Per-flight monitor memory (debounce timestamps + latches). Reset on arm."""
    return {"batt_low_since": None, "margin_low_since": None,
            "rtl_commanded_for": None, "standdown_for": None,
            "vibe_high_since": None, "clip_baseline": None,
            "airspeed_low_since": None, "bank_steep_since": None,
            "keepout_breach_since": None}


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
    # Same warnings, tagged with the monitor that produced them. The flat
    # `warnings` list stays exactly as it was (scenarios and the event log
    # match on its text), but the UI needs a STABLE key per warning: keyed by
    # monitor it can show every active warning with its own dismiss state,
    # instead of rendering warnings[0] and hiding the rest — which is a real
    # failure mode now that several monitors can warn at once.
    warning_items: list[dict] = []

    def _warn(monitor: str, text: str) -> None:
        warnings.append(text)
        warning_items.append({"monitor": monitor, "text": text})
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
        _warn("link", f"link {level} (heartbeats thinning)")

    # --- gps ---
    fix, sats = t.get("gps_fix", 0), t.get("gps_satellites", 0)
    gps_bad = armed and fix < 3
    gps_thin = armed and not gps_bad and sats < cfg.gps_min_sats
    monitors["gps"] = {"ok": not (gps_bad or gps_thin), "fix": fix, "sats": sats}
    if gps_bad:
        _warn("gps", f"GPS fix lost (fix={fix})")
        if cfg.gps_action == "rtl":
            action, reason, source = "rtl", f"GPS fix lost (fix={fix})", "gps"
    elif gps_thin:
        _warn("gps", f"GPS thin ({sats} sats < {cfg.gps_min_sats})")

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
        _warn("battery", f"battery {volts:.1f}V below warn {cfg.batt_warn_volt:.1f}V")
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
            _warn("rtl_margin", 
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

    # --- EKF health + variance ---
    ekf_healthy = bool(t.get("ekf_healthy", True))
    pos_var = t.get("ekf_pos_var") or 0.0
    vel_var = t.get("ekf_vel_var") or 0.0
    ekf_bad = armed and not ekf_healthy
    ekf_drifting = armed and not ekf_bad and (
        pos_var >= cfg.ekf_var_warn or vel_var >= cfg.ekf_var_warn)
    monitors["ekf"] = {"ok": not (ekf_bad or ekf_drifting),
                       "healthy": ekf_healthy,
                       "pos_var": round(pos_var, 3), "vel_var": round(vel_var, 3)}
    if ekf_bad:
        _warn("ekf", f"EKF unhealthy (flags={t.get('ekf_flags', 0)})")
        if cfg.ekf_action == "rtl" and action is None:
            action, reason, source = "rtl", "EKF unhealthy", "ekf"
    elif ekf_drifting:
        _warn("ekf", 
            f"EKF variance rising (pos={pos_var:.2f}, vel={vel_var:.2f} "
            f">= {cfg.ekf_var_warn:.2f})")

    # --- vibration ---
    vibe = max(t.get("vibration_x") or 0.0, t.get("vibration_y") or 0.0,
              t.get("vibration_z") or 0.0)
    clips = (t.get("clip_0") or 0, t.get("clip_1") or 0, t.get("clip_2") or 0)
    if armed and mem["clip_baseline"] is None:
        mem["clip_baseline"] = clips
    baseline = mem["clip_baseline"] or (0, 0, 0)
    new_clips = max(0, max(c - b for c, b in zip(clips, baseline)))
    vibe_high = armed and vibe >= cfg.vibe_warn_ms2
    if vibe_high:
        if mem["vibe_high_since"] is None:
            mem["vibe_high_since"] = now
    else:
        mem["vibe_high_since"] = None
    vibe_sustained = (mem["vibe_high_since"] is not None
                      and now - mem["vibe_high_since"] >= cfg.vibe_sustained_s)
    clip_high = armed and new_clips >= cfg.vibe_clip_warn
    monitors["vibration"] = {"ok": not (vibe_high or clip_high),
                             "peak_ms2": round(vibe, 1), "new_clips": new_clips}
    if vibe_high:
        _warn("vibration", f"vibration high ({vibe:.0f} m/s/s >= "
                        f"{cfg.vibe_warn_ms2:.0f})")
    if clip_high:
        _warn("vibration", f"accelerometer clipping ({new_clips} events this flight)")
    if (vibe_sustained or clip_high) and cfg.vibe_action == "rtl" and action is None:
        action, source = "rtl", "vibration"
        reason = (f"sustained high vibration/clipping "
                  f"(peak {vibe:.0f} m/s/s, {new_clips} clip events)")

    # --- airspeed / stall margin ---
    alt = t.get("altitude") or 0.0
    airspeed = t.get("airspeed") or 0.0
    airborne = armed and alt >= cfg.airborne_alt_m
    as_warn = airborne and airspeed < cfg.airspeed_warn_ms
    as_low = airborne and airspeed < cfg.airspeed_min_ms
    if as_low:
        if mem["airspeed_low_since"] is None:
            mem["airspeed_low_since"] = now
    else:
        mem["airspeed_low_since"] = None
    as_low_sustained = (mem["airspeed_low_since"] is not None
                        and now - mem["airspeed_low_since"] >= cfg.airspeed_low_s)
    monitors["airspeed"] = {"ok": not as_warn, "airspeed": round(airspeed, 1),
                            "airborne": airborne}
    if as_warn:
        _warn("airspeed", 
            f"airspeed low ({airspeed:.1f} m/s < warn {cfg.airspeed_warn_ms:.1f})"
            + (" — stall risk" if as_low else ""))
    if as_low_sustained and cfg.airspeed_action == "rtl" and action is None:
        action, source = "rtl", "airspeed"
        reason = (f"airspeed {airspeed:.1f} m/s below stall-risk floor "
                  f"{cfg.airspeed_min_ms:.1f} m/s for {cfg.airspeed_low_s:.0f}s")

    # --- bank angle ---
    # Reuses the airborne gate: a plane sitting on a slope, or banking during
    # the takeoff roll, is not a flight-safety event.
    roll_deg = abs(math.degrees(t.get("roll") or 0.0))
    bank_limit = cfg.bank_warn_deg
    low_and_slow = (cfg.bank_low_alt_m > 0 and alt < cfg.bank_low_alt_m)
    if low_and_slow:
        bank_limit *= cfg.bank_low_alt_factor
    bank_steep = airborne and roll_deg >= bank_limit
    if bank_steep:
        if mem["bank_steep_since"] is None:
            mem["bank_steep_since"] = now
    else:
        mem["bank_steep_since"] = None
    bank_sustained = (mem["bank_steep_since"] is not None
                      and now - mem["bank_steep_since"] >= cfg.bank_sustained_s)
    monitors["bank"] = {"ok": not bank_steep, "roll_deg": round(roll_deg, 1),
                        "limit_deg": round(bank_limit, 1),
                        "low_alt": bool(low_and_slow)}
    if bank_steep:
        _warn("bank", 
            f"bank {roll_deg:.0f} deg past {bank_limit:.0f}"
            + (" (tightened: low altitude)" if low_and_slow else ""))
    if bank_sustained and cfg.bank_action == "rtl" and action is None:
        action, source = "rtl", "bank"
        reason = (f"bank angle {roll_deg:.0f} deg held past {bank_limit:.0f} "
                  f"for {cfg.bank_sustained_s:.0f}s")

    # --- live keepout / hazard proximity ---
    # The distance itself is computed by keepout_watch and injected under
    # t["keepout"] — this module stays pure geometry-free logic. "known" is
    # reported separately from "ok" ON PURPOSE: no zone data means we cannot
    # judge, which must never render as a green tick.
    prox = t.get("keepout") or {}
    known = bool(prox.get("known"))
    haz_dist = prox.get("hazard_dist_m")
    breach = bool(prox.get("breach")) and armed
    if breach:
        if mem["keepout_breach_since"] is None:
            mem["keepout_breach_since"] = now
    else:
        mem["keepout_breach_since"] = None
    breach_sustained = (mem["keepout_breach_since"] is not None
                        and now - mem["keepout_breach_since"] >= cfg.keepout_sustained_s)
    monitors["keepout"] = {
        "ok": not breach, "known": known,
        "hazard_dist_m": (round(haz_dist, 1) if haz_dist is not None else None),
        "hazard_kind": prox.get("hazard_kind"),
        "keepout_dist_m": (round(prox["keepout_dist_m"], 1)
                           if prox.get("keepout_dist_m") is not None else None),
    }
    if breach:
        _warn("keepout", 
            f"{prox.get('hazard_kind') or 'hazard'} {haz_dist:.0f} m away — "
            f"inside the {prox.get('buffer_m', 0):.0f} m clearance")
    if breach_sustained and cfg.keepout_action == "rtl" and action is None:
        action, source = "rtl", "keepout"
        reason = (f"inside the clearance of a "
                  f"{prox.get('hazard_kind') or 'hazard'} keepout "
                  f"({haz_dist:.0f} m)")

    if not cfg.enabled:
        action, reason, source = None, None, None

    return {"monitors": monitors, "warnings": warnings,
            "warning_items": warning_items,
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
