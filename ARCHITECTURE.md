# UAV Software Architecture Directive

**Adopted 2026-07-22.** Standing engineering standards for this repository, provided verbatim
by the project owner (below the line). Read this before any structural change. Every change
should be reported against the directive's final-instruction checklist (why / Mission Planner
relation / ArduPilot relation / failure mode prevented / sim-tested / pre-flight work remaining).

## Interpretation notes (agreed in-session, 2026-07-22)

1. **Language:** the backend is Python 3.13 / FastAPI. TypeScript interface examples in the
   directive are treated as language-agnostic contracts — implemented as Pydantic models /
   typed dataclasses, not a TS rewrite.
2. **Airframe:** the directive references ArduCopter mode transitions; everything validated so
   far (aircraft, SITL, mode logic, takeoff/land flows) is fixed-wing **ArduPlane**. Resolution
   pending with the owner — until then, mode/mission logic stays ArduPlane-correct and new
   abstractions must not hardcode either airframe.
3. **Layer layout:** the `/src/...` layout maps onto `backend/app/` Python packages via
   incremental (strangler) refactor of a SITL-validated system — behavior-preserving moves with
   the 112-test suite green at every step, not a big-bang restructure.
4. **`npm run sim:*` scenarios:** map to backend automation entry points (Python) driving the
   bundled Windows SITL; scenario names and coverage stay as specified.
5. **Gap analysis first:** per the directive's own workflow (Analyze → Design → Implement),
   each milestone starts from the current audited gap list, highest-safety-impact first.

---

# Autonomous Agricultural Drone Software — Architecture & Refactor Directive for Claude Code

## Project Context

I am building the software stack for an autonomous agricultural drone that will ultimately integrate with a real aircraft running **ArduPilot**. The hardware engineer (Caleb) indicated that I need a much deeper understanding of **Mission Planner** before integration can proceed.

This is not a hobby drone project. The intended end state is a **commercial-grade autonomous UAV platform** capable of agricultural spraying operations, mission automation, telemetry monitoring, and future fleet management.

Your role is not to act as a code generator that blindly implements features. Your role is to act as a **senior UAV software architect** with deep experience in:

* ArduPilot
* MAVLink
* Mission Planner
* PX4 (for comparison)
* Companion computer architectures
* Autonomous mission systems
* Agricultural drone workflows
* Ground control station design
* Flight safety systems
* Embedded/edge robotics software
* Telemetry networks
* Production deployment practices

Every modification to this codebase should move it closer to a professional UAV software stack that could eventually be deployed on a real aircraft.

---

# Primary Objective

Continuously refactor, redesign, and improve the software so that it aligns with **professional ArduPilot + MAVLink development practices** and would be recognizable as a credible commercial UAV system to an experienced drone engineer.

Do not optimize for speed of implementation. Optimize for:

1. Correctness
2. Safety
3. Reliability
4. Maintainability
5. Extensibility
6. Real-world flight integration
7. Future autonomous operation

---

# Mental Model You Must Use

Before implementing any change, ask yourself these questions:

### ArduPilot Compatibility

* Would ArduPilot expect data in this format?
* Does this respect MAVLink message semantics?
* Are parameter names/types handled correctly?
* Are flight mode transitions valid for ArduCopter?
* Would this interfere with EKF, failsafes, or navigation logic?

### Mission Planner Integration

* Could Mission Planner connect simultaneously without conflicts?
* Would telemetry appear correctly in Mission Planner?
* Are parameters synchronized bidirectionally?
* Are missions compatible with Mission Planner's mission format?
* Would logs be analyzable in Mission Planner?

### Hardware Engineer Expectations

* Would a hardware engineer trust this software on a test aircraft?
* Is every command acknowledged and validated?
* Are unsafe operations blocked?
* Is state clearly observable from telemetry?
* Are errors diagnosable from logs?

### Production Readiness

* Can this recover from dropped telemetry links?
* Can components restart independently?
* Is persistent state stored safely?
* Can the system operate unattended?
* Can it scale to multiple aircraft later?

If the answer to any of these is **no**, redesign the approach before writing code.

---

# Assumed Target Hardware

Design the software assuming the following architecture unless told otherwise:

```
Ground Station (Laptop/Desktop)
        │
   Mission Planner
        │
        ├── MAVLink Telemetry (UDP/TCP/Serial)
        │
Custom GCS / Control Software
        │
        ├── MAVLink Router
        │
Companion Computer (Jetson / Pi / x86)
        │
        ├── MAVLink Serial Link
        │
Flight Controller (Cube / Pixhawk)
        │
        ├── GPS
        ├── IMU
        ├── Barometer
        ├── Magnetometer
        ├── Rangefinder
        ├── Flow Sensor
        └── Spray System Interfaces
```

The software must eventually support operation both on the **ground station** and on the **companion computer**.

---

# Architecture Requirements

## 1. Strict Layer Separation

The codebase must be organized into independent layers:

```
/src
  /core
  /mavlink
  /vehicle
  /missions
  /telemetry
  /parameters
  /failsafe
  /logging
  /spraying
  /ui
  /sim
  /tests
```

### core/

* Application lifecycle
* Dependency injection
* Configuration management
* Event bus
* State persistence

### mavlink/

* MAVLink transport abstraction
* Message serialization/deserialization
* Heartbeat management
* Command/ack handling
* Routing and multiplexing

### vehicle/

* Vehicle state machine
* Flight mode management
* Arming/disarming logic
* Health monitoring
* EKF/GPS status interpretation

### missions/

* Mission model
* Waypoint generation
* Mission upload/download
* Coverage planning
* Resume/restart logic

### telemetry/

* Telemetry stream manager
* Bandwidth throttling
* Historical buffering
* Real-time subscriptions

### parameters/

* Parameter cache
* Sync engine
* Validation rules
* Change tracking

### failsafe/

* Link loss handling
* GPS loss handling
* Battery failsafe coordination
* Emergency RTL/Land actions

### logging/

* Structured logs
* Flight event logs
* MAVLink packet logs
* Replay support

### spraying/

* Flow control
* Section control
* Application rate management
* Coverage verification

### sim/

* SITL integration
* Virtual telemetry
* Scenario testing
* Fault injection

No layer may directly access another layer's internal state. Use interfaces/events only.

---

# MAVLink Requirements

## Mandatory Message Support

Implement full support for at minimum:

* HEARTBEAT
* SYS_STATUS
* BATTERY_STATUS
* GPS_RAW_INT
* GLOBAL_POSITION_INT
* LOCAL_POSITION_NED
* ATTITUDE
* VFR_HUD
* STATUSTEXT
* PARAM_REQUEST_LIST
* PARAM_REQUEST_READ
* PARAM_SET
* PARAM_VALUE
* MISSION_COUNT
* MISSION_ITEM_INT
* MISSION_REQUEST_INT
* MISSION_ACK
* COMMAND_LONG
* COMMAND_ACK
* RC_CHANNELS
* SERVO_OUTPUT_RAW
* AUTOPILOT_VERSION

Use **MISSION_ITEM_INT** instead of deprecated MISSION_ITEM.

---

## Connection Management

Support:

* Serial
* UDP client/server
* TCP client/server
* Automatic reconnection
* Heartbeat timeout detection
* Multi-endpoint routing

Connection state machine:

```
DISCONNECTED
  → CONNECTING
  → WAITING_HEARTBEAT
  → SYNCHRONIZING
  → READY
  → DEGRADED
  → LOST
```

Every transition must emit an event and be logged.

---

# Vehicle State Model

Represent the aircraft with a strongly typed state object.

```typescript
interface VehicleState {
  connected: boolean;
  armed: boolean;
  mode: FlightMode;
  gpsFix: number;
  satellites: number;
  batteryVoltage: number;
  batteryRemaining: number;
  altitude: number;
  groundspeed: number;
  heading: number;
  ekfHealthy: boolean;
  linkQuality: number;
  lastHeartbeat: Date;
}
```

Never allow UI components to infer state directly from raw MAVLink packets.

---

# Mission System Requirements

## Mission Representation

Use an internal mission model independent of MAVLink:

```typescript
interface Mission {
  id: string;
  name: string;
  waypoints: Waypoint[];
  metadata: MissionMetadata;
}
```

Convert to MAVLink only at the transport boundary.

---

## Agricultural Coverage Planning

Support:

* Polygon field boundaries
* Headlands/buffer zones
* Swath spacing
* Alternate row direction
* Terrain following preparation
* Coverage overlap analysis
* Resume after interruption

Design the planner so terrain-aware path generation can be added later without redesigning the API.

---

# Parameter Synchronization Engine

This is one of the most important systems.

Requirements:

* Full parameter download on connect
* Incremental updates
* Change detection
* Conflict resolution
* Local cache persistence
* Type-safe accessors
* Validation against known ranges
* Batch updates with rollback

Example API:

```typescript
await params.set("WPNAV_SPEED", 1000);
await params.set("RTL_ALT", 1500);
await params.commit();
```

If any parameter fails, automatically revert the entire batch.

---

# Failsafe Architecture

Implement independent failsafe monitors.

## Telemetry Link Failsafe

Levels:

* Warning: >1s heartbeat gap
* Degraded: >3s
* Lost: >5s
* Critical: >10s

Actions configurable per level.

---

## GPS Failsafe

* Detect fix degradation
* Detect HDOP spikes
* Detect satellite count collapse
* Trigger navigation restrictions

---

## Battery Failsafe

* Voltage threshold
* Remaining percentage
* Estimated remaining flight time
* Automatic RTL recommendation

---

## Emergency State Machine

```
NORMAL
 → WARNING
 → RTL_REQUESTED
 → RTL_ACTIVE
 → LANDING
 → DISARMED
```

Never issue emergency commands without recording the triggering condition.

---

# Logging Standards

Use structured JSON logs.

Example:

```json
{
  "ts": "2026-07-22T20:15:32.123Z",
  "level": "WARN",
  "component": "telemetry",
  "event": "heartbeat_timeout",
  "vehicle": 1,
  "gap_ms": 3200
}
```

Required log categories:

* Connection
* MAVLink packets
* Parameter changes
* Mission operations
* Flight mode changes
* Arming/disarming
* Failsafe events
* Spray system events
* Exceptions/crashes

Logs must be replayable for post-flight analysis.

---

# SITL Integration (Mandatory)

Before any hardware testing, all features must run in **ArduPilot SITL**.

Automate:

* Launching SITL
* Connecting via UDP
* Uploading missions
* Simulated takeoff
* Simulated mission execution
* Injecting telemetry loss
* Injecting GPS loss
* Injecting battery faults

Create reproducible scenarios:

```bash
npm run sim:field-test
npm run sim:link-loss
npm run sim:gps-failure
npm run sim:rtl-recovery
```

No feature is considered complete until it passes simulation tests.

---

# Mission Planner Compatibility Checklist

For every milestone, verify:

* [ ] Mission Planner can connect simultaneously
* [ ] Vehicle appears correctly
* [ ] Parameters synchronize correctly
* [ ] Missions upload/download correctly
* [ ] Flight modes remain synchronized
* [ ] STATUSTEXT messages appear correctly
* [ ] DataFlash logs remain valid
* [ ] MAVLink Inspector shows no malformed packets
* [ ] No duplicate system IDs are created
* [ ] Heartbeat rate remains within expected range

---

# What Caleb Is Probably Expecting

The hardware engineer is likely imagining software that behaves more like this:

### Expected Behavior

* Connects cleanly to a Pixhawk
* Downloads all parameters automatically
* Detects aircraft configuration
* Shows health status immediately
* Can upload a mission without Mission Planner
* Monitors telemetry continuously
* Handles reconnection automatically
* Logs every command and acknowledgment
* Can be tested entirely in SITL before touching hardware

### Red Flags He Would Notice

* Hardcoded MAVLink message IDs
* No ACK handling
* UI directly sending commands
* No parameter cache
* No heartbeat monitoring
* No state machine
* No simulation support
* No structured logging
* No separation between mission logic and transport

Avoid all of these.

---

# Refactor Policy

Whenever you encounter existing code:

### Keep

* Clean abstractions
* Type-safe models
* Well-tested utilities

### Refactor

* Tight coupling
* Global state
* Duplicate MAVLink handling
* UI-driven business logic
* Synchronous network operations

### Replace Entirely

* Unsafe command execution
* Blocking telemetry loops
* Unvalidated parameter writes
* Ad-hoc mission serialization
* Inconsistent state representations

When refactoring, explain:

1. What was wrong
2. Why it is dangerous in a UAV context
3. How the new design improves reliability
4. Any migration considerations

---

# Development Workflow

For every task, follow this sequence:

## 1. Analyze

* Understand ArduPilot expectations
* Identify safety implications
* Identify architectural impact

## 2. Design

* Propose interfaces
* Define state transitions
* Define failure modes

## 3. Implement

* Write production-quality code
* Add structured logging
* Add telemetry hooks

## 4. Test

* Unit tests
* SITL integration tests
* Failure injection tests

## 5. Review

* Identify technical debt
* Suggest future improvements
* Evaluate Mission Planner compatibility

Never skip the design phase.

---

# Long-Term Roadmap (Design For This Now)

The architecture must be capable of eventually supporting:

### Phase 1 — Current

* Single drone
* Manual mission upload
* Telemetry monitoring

### Phase 2

* Autonomous field missions
* Spray control integration
* Coverage analytics

### Phase 3

* Terrain following
* Obstacle avoidance integration
* RTK positioning

### Phase 4

* Multi-drone fleet management
* Cloud mission synchronization
* Remote telemetry relays
* OTA software updates

### Phase 5

* Fully autonomous agricultural operations
* Dynamic mission replanning
* AI-assisted coverage optimization
* Fleet scheduling and dispatch

Do not hardcode assumptions that would prevent these future phases.

---

# Final Instruction

From this point forward, treat this repository as if it were the foundation of a venture-backed commercial UAV platform that will eventually be tested on a real aircraft.

Whenever you make a change, explicitly state:

* **Why this change is necessary**
* **How it relates to Mission Planner**
* **How it relates to ArduPilot**
* **What failure mode it prevents**
* **Whether it is simulation-tested**
* **What additional work would be required before real flight testing**

If you are uncertain about any MAVLink or ArduPilot behavior, stop and explain the uncertainty and the possible implementations instead of guessing.

The objective is not merely to make the software work.

The objective is to build a UAV software stack that a professional drone engineer would consider **credible, testable, and safe enough to begin hardware integration with a Pixhawk-based agricultural aircraft**.
