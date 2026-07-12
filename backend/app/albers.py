"""EPSG:5070 (NAD83 / CONUS Albers Equal-Area) forward + inverse projection.

The USDA Cropland Data Layer is served in this grid, so field auto-detection
needs to move between lat/lon and Albers meters. Pure-python Snyder formulas
(Map Projections — A Working Manual, USGS PP 1395) on the GRS80 ellipsoid.
"""

import math

# GRS80 ellipsoid + EPSG:5070 parameters
_A = 6378137.0
_F = 1.0 / 298.257222101
_E2 = 2 * _F - _F * _F
_E = math.sqrt(_E2)
_PHI1 = math.radians(29.5)   # standard parallel 1
_PHI2 = math.radians(45.5)   # standard parallel 2
_PHI0 = math.radians(23.0)   # latitude of origin
_LAM0 = math.radians(-96.0)  # central meridian


def _m(phi):
    return math.cos(phi) / math.sqrt(1 - _E2 * math.sin(phi) ** 2)


def _q(phi):
    s = math.sin(phi)
    return (1 - _E2) * (s / (1 - _E2 * s * s)
                        - (1 / (2 * _E)) * math.log((1 - _E * s) / (1 + _E * s)))


_N = (_m(_PHI1) ** 2 - _m(_PHI2) ** 2) / (_q(_PHI2) - _q(_PHI1))
_C = _m(_PHI1) ** 2 + _N * _q(_PHI1)
_RHO0 = _A * math.sqrt(_C - _N * _q(_PHI0)) / _N


def to_albers(lat: float, lon: float):
    """(lat, lon) degrees -> (x, y) EPSG:5070 meters."""
    phi = math.radians(lat)
    lam = math.radians(lon)
    rho = _A * math.sqrt(_C - _N * _q(phi)) / _N
    theta = _N * (lam - _LAM0)
    return rho * math.sin(theta), _RHO0 - rho * math.cos(theta)


def from_albers(x: float, y: float):
    """(x, y) EPSG:5070 meters -> (lat, lon) degrees. Iterative inverse."""
    rho = math.hypot(x, _RHO0 - y)
    theta = math.atan2(x, _RHO0 - y)
    q = (_C - (rho * _N / _A) ** 2) / _N
    lam = _LAM0 + theta / _N

    # Iterate the latitude (converges in a handful of steps).
    phi = math.asin(max(-1.0, min(1.0, q / 2)))
    for _ in range(15):
        s = math.sin(phi)
        denom = 1 - _E2 * s * s
        corr = ((denom ** 2) / (2 * math.cos(phi))) * (
            q / (1 - _E2) - s / denom
            + (1 / (2 * _E)) * math.log((1 - _E * s) / (1 + _E * s)))
        phi += corr
        if abs(corr) < 1e-12:
            break
    return math.degrees(phi), math.degrees(lam)
