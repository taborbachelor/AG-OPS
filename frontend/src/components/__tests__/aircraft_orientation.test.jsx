/**
 * 3D aircraft pose — frame conventions, pinned.
 *
 * This was the standing "eyeball it on the next start-all" item: does the
 * nose lead the trail, or does the aircraft fly sideways? It does not need
 * eyes. Cesium is a dependency, so the real library can be asked directly
 * where the model's nose ends up, and the answer checked against the physical
 * requirement: at heading H the nose must point along bearing H.
 *
 * It was in fact wrong, in two independent ways:
 *
 *  - HEADING was 90 deg off. headingPitchRollQuaternion resolves the body
 *    frame against an EAST-north-up frame, so body +X points east at heading
 *    0, while the model is built nose-along-+X. Measured before the fix:
 *    heading 0 -> bearing 90.00, heading 90 -> bearing 180.00.
 *  - PITCH was inverted. The backend stores MAVLink ATTITUDE pitch unmodified
 *    (positive = nose up) and Cesium's positive pitch is also nose up, but the
 *    view negated it, so a climbing aircraft rendered nose-down.
 *
 * Roll was already correct and is pinned here so a fix to the other two
 * cannot quietly break it.
 *
 * These assert against the exported pose functions, not a copy of the maths.
 */
import * as Cesium from 'cesium';
import { describe, it, expect } from 'vitest';
import { aircraftQuaternion } from '../MapView3D';

const LAT = 39.9042, LON = -95.7997, ALT = 100;
const origin = Cesium.Cartesian3.fromDegrees(LON, LAT, ALT);

const enuAxis = (i) => {
  const m = Cesium.Transforms.eastNorthUpToFixedFrame(origin);
  const v = Cesium.Matrix4.getColumn(m, i, new Cesium.Cartesian4());
  return new Cesium.Cartesian3(v.x, v.y, v.z);
};
const EAST = enuAxis(0), NORTH = enuAxis(1), UP = enuAxis(2);

function bodyAxes(telem) {
  const q = aircraftQuaternion(origin, telem);
  const m = Cesium.Matrix3.fromQuaternion(q, new Cesium.Matrix3());
  return {
    nose: Cesium.Matrix3.getColumn(m, 0, new Cesium.Cartesian3()),
    wing: Cesium.Matrix3.getColumn(m, 1, new Cesium.Cartesian3()),
  };
}

const bearingOf = (v) => (Cesium.Math.toDegrees(Math.atan2(
  Cesium.Cartesian3.dot(v, EAST), Cesium.Cartesian3.dot(v, NORTH))) + 360) % 360;

describe('3D aircraft orientation', () => {
  it.each([0, 45, 90, 180, 270, 359])('nose leads the track at heading %i', (heading) => {
    const { nose } = bodyAxes({ heading, pitch: 0, roll: 0 });
    expect(bearingOf(nose)).toBeCloseTo(heading, 1);
  });

  it('renders a climb nose-UP, not nose-down', () => {
    const { nose } = bodyAxes({ heading: 0, pitch: Cesium.Math.toRadians(10), roll: 0 });
    // Positive ArduPilot pitch is nose up, so the nose must gain altitude.
    expect(Cesium.Cartesian3.dot(nose, UP)).toBeGreaterThan(0.1);
  });

  it('renders a dive nose-DOWN', () => {
    const { nose } = bodyAxes({ heading: 0, pitch: Cesium.Math.toRadians(-10), roll: 0 });
    expect(Cesium.Cartesian3.dot(nose, UP)).toBeLessThan(-0.1);
  });

  it('keeps the nose on its bearing while pitched', () => {
    const { nose } = bodyAxes({ heading: 90, pitch: Cesium.Math.toRadians(15), roll: 0 });
    expect(bearingOf(nose)).toBeCloseTo(90, 1);
  });

  it('banks the correct way: positive roll drops the right wing', () => {
    // Body +Y is the left wing, so a right-wing-down roll lifts it.
    const { wing } = bodyAxes({ heading: 0, pitch: 0, roll: Cesium.Math.toRadians(10) });
    expect(Cesium.Cartesian3.dot(wing, UP)).toBeGreaterThan(0.1);
  });

  it('is level when the aircraft is level', () => {
    const { nose, wing } = bodyAxes({ heading: 0, pitch: 0, roll: 0 });
    expect(Cesium.Cartesian3.dot(nose, UP)).toBeCloseTo(0, 6);
    expect(Cesium.Cartesian3.dot(wing, UP)).toBeCloseTo(0, 6);
  });

  it('tolerates a telemetry sample with no attitude yet', () => {
    expect(() => bodyAxes({})).not.toThrow();
    expect(bearingOf(bodyAxes({}).nose)).toBeCloseTo(0, 1);
  });
});
