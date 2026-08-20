#!/usr/bin/env python
r"""Tests for the seam checker.

Every case here is a false negative or false positive that would make the tool
worthless in a specific, already-observed way. The first one is the bug the tool
exists for: when the keepout proximity monitor ran with zero rings, GET
/api/safety/keepouts WAS called from the frontend and only POST was orphaned, so
a path-only check reports that route reachable and finds nothing.

    py tools\tests\test_seam_check.py
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import seam_check  # noqa: E402


def build(tmp, main, routers, callers):
    """Write a throwaway mini-repo and point the checker at it."""
    os.makedirs(os.path.join(tmp, "backend/app/routers"), exist_ok=True)
    os.makedirs(os.path.join(tmp, "frontend/src"), exist_ok=True)
    os.makedirs(os.path.join(tmp, "backend/tests"), exist_ok=True)

    with open(os.path.join(tmp, "backend/app/main.py"), "w") as f:
        f.write(main)
    for name, body in routers.items():
        with open(os.path.join(tmp, "backend/app/routers", name), "w") as f:
            f.write(body)
    for name, body in callers.items():
        with open(os.path.join(tmp, name), "w") as f:
            f.write(body)
    seam_check.ROOT = tmp


class Base(unittest.TestCase):
    def setUp(self):
        self._real_root = seam_check.ROOT
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        seam_check.ROOT = self._real_root

    def orphans(self):
        """Drives the REAL find_orphans, never a restatement of it."""
        routes = seam_check.find_routes()
        calls, murky = seam_check.find_calls()
        found = seam_check.find_orphans(routes, calls)
        return [(r["method"], r["display"]) for r, _kinds in found], murky


MAIN = ('app.include_router(safety.router, prefix="/api/safety", tags=["s"])\n')
SAFETY = ('@router.get("/keepouts")\ndef a(): pass\n'
          '@router.post("/keepouts")\ndef b(): pass\n')


class TestMethodSensitivity(Base):
    def test_a_get_caller_does_not_cover_the_post_route(self):
        """The keepout bug, exactly. A path-only check finds nothing here."""
        build(self.tmp, MAIN, {"safety.py": SAFETY},
              {"frontend/src/Panel.jsx":
               "const r = await fetch(`${API}/safety/keepouts`);\n"})
        orphans, _ = self.orphans()
        self.assertIn(("POST", "/api/safety/keepouts"), orphans,
                      "the orphaned POST was reported as reachable")
        self.assertNotIn(("GET", "/api/safety/keepouts"), orphans)

    def test_the_post_caller_clears_it(self):
        build(self.tmp, MAIN, {"safety.py": SAFETY},
              {"frontend/src/Panel.jsx":
               "await fetch(`${API}/safety/keepouts`, { method: 'POST' });\n"
               "await fetch(`${API}/safety/keepouts`);\n"})
        orphans, _ = self.orphans()
        self.assertEqual(orphans, [], "a real caller was not credited")


class TestBaseConstants(Base):
    def test_a_file_declaring_its_own_base_is_resolved(self):
        """web/ uses API_BASE = '/api/orders'; reading it as /api reported four
        live order endpoints as orphaned, which is how a checker gets ignored."""
        build(self.tmp,
              'app.include_router(orders.router, prefix="/api/orders", tags=["o"])\n',
              {"orders.py": '@router.post("/{order_id}/pay")\ndef p(): pass\n'},
              {"frontend/src/Order.jsx":
               "const API_BASE = '/api/orders'\n"
               "await fetch(`${API_BASE}/${id}/pay`, { method: 'POST' })\n"})
        orphans, _ = self.orphans()
        self.assertEqual(orphans, [])

    def test_a_path_param_matches_an_interpolated_segment(self):
        build(self.tmp,
              'app.include_router(logs.router, prefix="/api/logs", tags=["l"])\n',
              {"logs.py": '@router.get("/{name}")\ndef g(): pass\n'},
              {"frontend/src/Logs.jsx": "await fetch(`${API}/logs/${n}`)\n"})
        orphans, _ = self.orphans()
        self.assertEqual(orphans, [])

    def test_a_shorter_path_does_not_satisfy_a_longer_route(self):
        """/api/logs must not be credited as a caller of /api/logs/{name}."""
        build(self.tmp,
              'app.include_router(logs.router, prefix="/api/logs", tags=["l"])\n',
              {"logs.py": '@router.get("/{name}")\ndef g(): pass\n'},
              {"frontend/src/Logs.jsx": "await fetch(`${API}/logs`)\n"})
        orphans, _ = self.orphans()
        self.assertIn(("GET", "/api/logs/{name}"), orphans)


class TestHonesty(Base):
    def test_a_runtime_built_path_is_declared_not_counted(self):
        """Silently treating an unresolvable call as coverage is the one
        failure mode that would make this tool lie in the safe direction."""
        build(self.tmp, MAIN, {"safety.py": SAFETY},
              {"frontend/src/Panel.jsx": "await fetch(`${API}${path}`)\n"})
        orphans, murky = self.orphans()
        self.assertTrue(murky, "an unresolvable call site was not declared")
        self.assertIn(("POST", "/api/safety/keepouts"), orphans)

    def test_a_test_only_caller_still_counts_as_a_caller(self):
        build(self.tmp, MAIN, {"safety.py": SAFETY},
              {"backend/tests/test_x.py":
               'client.post("/api/safety/keepouts")\n'
               'client.get("/api/safety/keepouts")\n'})
        orphans, _ = self.orphans()
        self.assertEqual(orphans, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
