#!/usr/bin/env python
"""Acceptance tests for the AgOps coordination layer.

Run:  py tools\\tests\\test_agops.py          (or via pytest)

Every test here maps to a behaviour the team actually depends on. The two that
matter most are the ones that cannot be talked into working: the atomic-claim
race (test 4) spawns REAL concurrent processes rather than calling the function
twice in a row, and the crash test (test 10) asserts that recovery preserves
work rather than freeing it.

All state goes to a temp AGOPS_HOME, so running the suite never touches the live
team board.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest

_TMP = tempfile.mkdtemp(prefix="agops-test-")
os.environ["AGOPS_HOME"] = _TMP          # MUST precede the core import

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)

from agops import core  # noqa: E402
from agops.core import AgopsError  # noqa: E402

CLI = os.path.join(TOOLS, "agops.py")


def write_config(**over):
    cfg = core.default_config()
    cfg.update({
        "project_id": "agops-gcs",
        "project_name": "AgOps GCS",
        "areas": {
            "PLANNER": {"desc": "planning", "globs": ["backend/app/coverage*.py"]},
            "UI": {"desc": "frontend", "globs": ["frontend/*", "frontend/**"]},
        },
    })
    cfg.update(over)
    core.save_config(cfg)


def reset():
    for suffix in ("", "-wal", "-shm"):
        p = core.DB_PATH + suffix
        if os.path.exists(p):
            os.remove(p)
    write_config()


def run_cli(*args, env=None):
    e = dict(os.environ)
    e["AGOPS_HOME"] = _TMP
    if env:
        e.update(env)
    return subprocess.run([sys.executable, CLI] + list(args),
                          capture_output=True, text=True, env=e, timeout=60)


class Base(unittest.TestCase):
    def setUp(self):
        reset()


# --- 1 ------------------------------------------------------------------------

class TestRegistration(Base):
    def test_three_agents_register_and_appear(self):
        for sid, name in (("s-a", "alpha"), ("s-b", "bravo"), ("s-c", "charlie")):
            core.register_agent(session_id=sid, name=name)
        names = [a["name"] for a in core.list_agents()]
        self.assertEqual(sorted(names), ["alpha", "bravo", "charlie"])
        for a in core.list_agents():
            self.assertEqual(a["project_id"], "agops-gcs")
            self.assertFalse(a["stale"])

    def test_names_are_deterministic_when_unspecified(self):
        # The Nth agent to ever join gets the Nth NATO name, so names are stable
        # across runs instead of shuffling.
        for i in range(4):
            core.register_agent(session_id="s-%d" % i)
        self.assertEqual([a["name"] for a in core.list_agents()],
                         ["alpha", "bravo", "charlie", "delta"])

    def test_existing_names_are_never_reassigned(self):
        core.register_agent(session_id="s-a", name="alpha")
        core.register_agent(session_id="s-x")
        names = {a["name"] for a in core.list_agents()}
        self.assertIn("alpha", names)
        self.assertNotIn("alpha", {a["name"] for a in core.list_agents()
                                   if a["agent_id"] == "s-x"})


# --- 2 ------------------------------------------------------------------------

class TestProjectIsolation(Base):
    def test_foreign_project_id_is_refused(self):
        core.register_agent(session_id="s-a", name="alpha")
        with self.assertRaises(AgopsError) as ctx:
            core.list_agents(project="some-other-repo")
        self.assertIn("project mismatch", str(ctx.exception))

    def test_every_coordination_entry_point_is_guarded(self):
        for call in (lambda: core.project_status(project="other"),
                     lambda: core.create_task("x", project="other"),
                     lambda: core.claim_task("TASK-001", "alpha", project="other"),
                     lambda: core.send_message("a", "b", "hi", project="other"),
                     lambda: core.check_conflicts(["a.py"], project="other")):
            with self.assertRaises(AgopsError):
                call()

    def test_state_lives_inside_the_repo(self):
        # The second, structural barrier: coordination state is a file in the
        # project, so a session in another checkout cannot see this team at all.
        self.assertTrue(core.DB_PATH.startswith(core.AGOPS_DIR))


# --- 3 ------------------------------------------------------------------------

class TestTaskCreation(Base):
    def test_task_without_dependencies_is_immediately_available(self):
        t = core.create_task("Add MAVLink telemetry parser", priority="HIGH")
        self.assertEqual(t["task_id"], "TASK-001")
        self.assertEqual(t["status"], "AVAILABLE")
        self.assertIn("TASK-001", [x["task_id"] for x in
                                   core.list_tasks(status="AVAILABLE")])

    def test_ids_increment(self):
        core.create_task("one")
        self.assertEqual(core.create_task("two")["task_id"], "TASK-002")

    def test_bad_priority_refused(self):
        with self.assertRaises(AgopsError):
            core.create_task("x", priority="URGENT")


# --- 4 + 5 --------------------------------------------------------------------

class TestAtomicClaiming(Base):
    def test_second_claimer_loses_and_is_told_who_won(self):
        core.register_agent(session_id="s-a", name="alpha")
        core.register_agent(session_id="s-b", name="bravo")
        core.create_task("shared work")
        first = core.claim_task("TASK-001", "alpha")
        second = core.claim_task("TASK-001", "bravo")
        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertEqual(second["owner"], "alpha")
        self.assertIn("already owned by alpha", second["message"])

    def test_concurrent_processes_produce_exactly_one_winner(self):
        # The real thing: six separate OS processes racing the same claim. A
        # read-then-write implementation passes the sequential test above and
        # fails this one.
        names = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"]
        for i, n in enumerate(names):
            core.register_agent(session_id="s-%d" % i, name=n)
        core.create_task("contended")
        results, start = [], threading.Barrier(len(names))

        def go(name):
            start.wait()
            r = run_cli("claim", "TASK-001", "--agent", name, "--json")
            results.append((name, r.returncode, r.stdout))

        threads = [threading.Thread(target=go, args=(n,)) for n in names]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        winners = [n for n, rc, _ in results if rc == 0]
        self.assertEqual(len(winners), 1,
                         "expected exactly one winner, got %r" % winners)
        owner = core.get_task("TASK-001")["owner"]
        self.assertEqual(owner, winners[0])
        for name, rc, sout in results:
            if rc != 0:
                self.assertIn(owner, sout,
                              "loser %s was not told who owns it" % name)

    def test_loser_cannot_act_as_owner(self):
        core.register_agent(session_id="s-a", name="alpha")
        core.register_agent(session_id="s-b", name="bravo")
        core.create_task("shared work")
        core.claim_task("TASK-001", "alpha")
        core.claim_task("TASK-001", "bravo")
        # Pretending is not enough: ownership is checked on every mutation.
        with self.assertRaises(AgopsError):
            core.complete_task("TASK-001", "bravo", "I did it, honestly")
        with self.assertRaises(AgopsError):
            core.release_task("TASK-001", "bravo")
        self.assertEqual(core.get_task("TASK-001")["owner"], "alpha")

    def test_reclaiming_your_own_task_is_not_an_error(self):
        core.register_agent(session_id="s-a", name="alpha")
        core.create_task("mine")
        core.claim_task("TASK-001", "alpha")
        again = core.claim_task("TASK-001", "alpha")
        self.assertTrue(again["ok"])


# --- 6 ------------------------------------------------------------------------

class TestDependencies(Base):
    def test_dependent_task_is_blocked_then_becomes_available(self):
        core.register_agent(session_id="s-a", name="alpha")
        core.create_task("foundation")
        dep = core.create_task("built on top", depends_on=["TASK-001"])
        self.assertEqual(dep["status"], "BLOCKED")
        self.assertIn("TASK-001", dep["blocked_reason"])

        core.claim_task("TASK-001", "alpha")
        res = core.complete_task("TASK-001", "alpha",
                                 "foundation landed; 12 unit tests green", tests_passed=True)
        self.assertEqual(res["unblocked"], ["TASK-002"])
        self.assertEqual(core.get_task("TASK-002")["status"], "AVAILABLE")

    def test_a_blocked_task_cannot_be_claimed(self):
        core.register_agent(session_id="s-a", name="alpha")
        core.create_task("foundation")
        core.create_task("later", depends_on=["TASK-001"])
        with self.assertRaises(AgopsError):
            core.claim_task("TASK-002", "alpha")

    def test_chain_unblocks_one_level_at_a_time(self):
        core.register_agent(session_id="s-a", name="alpha")
        core.create_task("a")
        core.create_task("b", depends_on=["TASK-001"])
        core.create_task("c", depends_on=["TASK-002"])
        core.claim_task("TASK-001", "alpha")
        core.complete_task("TASK-001", "alpha",
                           "task a is done and its unit tests pass")
        self.assertEqual(core.get_task("TASK-002")["status"], "AVAILABLE")
        self.assertEqual(core.get_task("TASK-003")["status"], "BLOCKED")

    def test_multiple_prerequisites_all_required(self):
        core.register_agent(session_id="s-a", name="alpha")
        core.create_task("a")
        core.create_task("b")
        core.create_task("c", depends_on=["TASK-001", "TASK-002"])
        core.claim_task("TASK-001", "alpha")
        core.complete_task("TASK-001", "alpha",
                           "first prerequisite done and verified")
        self.assertEqual(core.get_task("TASK-003")["status"], "BLOCKED")
        core.claim_task("TASK-002", "alpha")
        core.complete_task("TASK-002", "alpha",
                           "second prerequisite done and verified")
        self.assertEqual(core.get_task("TASK-003")["status"], "AVAILABLE")


# --- 7 ------------------------------------------------------------------------

class TestFileConflicts(Base):
    def setUp(self):
        super().setUp()
        core.register_agent(session_id="s-a", name="alpha")
        core.register_agent(session_id="s-b", name="bravo")

    def test_direct_file_overlap_blocks_a_claim(self):
        core.create_task("refactor spacing", files=["src/example.ts"])
        core.create_task("also spacing", files=["src/example.ts"])
        core.claim_task("TASK-001", "alpha")
        res = core.claim_task("TASK-002", "bravo")
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "conflict")
        self.assertEqual(res["conflicts"][0]["level"], "BLOCKING")
        self.assertEqual(res["conflicts"][0]["owner"], "alpha")

    def test_non_overlapping_files_are_free(self):
        core.create_task("one", files=["src/a.ts"])
        core.create_task("two", files=["src/b.ts"])
        core.claim_task("TASK-001", "alpha")
        self.assertTrue(core.claim_task("TASK-002", "bravo")["ok"])

    def test_area_overlap_is_advisory_not_blocking(self):
        # Directory ownership must never freeze fine-grained parallel work.
        core.create_task("planner work", files=["backend/app/coverage.py"],
                         area="PLANNER")
        core.create_task("other planner work",
                         files=["backend/app/coverage_multi.py"], area="PLANNER")
        core.claim_task("TASK-001", "alpha")
        res = core.claim_task("TASK-002", "bravo")
        self.assertTrue(res["ok"], "advisory area overlap must not block")
        self.assertTrue(res["warnings"])
        self.assertEqual(res["warnings"][0]["level"], "WARNING")

    def test_file_conflict_outranks_area_advisory(self):
        core.create_task("a", files=["backend/app/coverage.py"], area="PLANNER")
        core.create_task("b", files=["backend/app/coverage.py"], area="PLANNER")
        core.claim_task("TASK-001", "alpha")
        res = core.claim_task("TASK-002", "bravo")
        self.assertFalse(res["ok"])
        self.assertEqual(res["conflicts"][0]["level"], "BLOCKING")

    def test_glob_overlap_is_detected(self):
        core.create_task("all planner tests", files=["backend/tests/test_cov*.py"])
        core.create_task("one planner test",
                         files=["backend/tests/test_coverage.py"])
        core.claim_task("TASK-001", "alpha")
        self.assertFalse(core.claim_task("TASK-002", "bravo")["ok"])

    def test_stale_owner_does_not_block_new_work(self):
        core.create_task("a", files=["src/x.ts"])
        core.create_task("b", files=["src/x.ts"])
        core.claim_task("TASK-001", "alpha")
        conn = core.connect()
        conn.execute("UPDATE agents SET last_heartbeat=? WHERE name='alpha'",
                     (core._now() - 99999,))
        conn.close()
        self.assertEqual(core.check_conflicts(["src/x.ts"], agent="bravo")["level"],
                         "NONE")

    def test_force_is_available_after_agreeing(self):
        core.create_task("a", files=["src/x.ts"])
        core.create_task("b", files=["src/x.ts"])
        core.claim_task("TASK-001", "alpha")
        self.assertTrue(core.claim_task("TASK-002", "bravo", force=True)["ok"])

    def test_get_file_owners_names_the_agent(self):
        core.create_task("a", files=["src/x.ts"])
        core.claim_task("TASK-001", "alpha")
        self.assertEqual(core.get_file_owners("src/x.ts")["owners"], ["alpha"])


# --- 8 + 9 --------------------------------------------------------------------

class TestMessaging(Base):
    def setUp(self):
        super().setUp()
        for sid, n in (("s-a", "alpha"), ("s-b", "bravo"), ("s-c", "charlie")):
            core.register_agent(session_id=sid, name=n)

    def test_direct_message_is_delivered_once(self):
        core.send_message("alpha", "bravo",
                          "waypoint altitude is metres AGL now", "WARNING")
        got = core.inbox("bravo")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["sender"], "alpha")
        self.assertEqual(got[0]["msg_type"], "WARNING")
        self.assertEqual(core.inbox("bravo"), [], "message re-delivered after read")

    def test_message_is_not_visible_to_other_agents(self):
        core.send_message("alpha", "bravo", "for bravo only")
        self.assertEqual(core.inbox("charlie"), [])

    def test_unknown_recipient_refused(self):
        with self.assertRaises(AgopsError):
            core.send_message("alpha", "zulu", "hello?")

    def test_broadcast_reaches_everyone_but_the_sender(self):
        core.send_message("alpha", "ALL", "breaking: mission schema v2", "WARNING")
        self.assertEqual(len(core.inbox("bravo")), 1)
        self.assertEqual(len(core.inbox("charlie")), 1)
        self.assertEqual(core.inbox("alpha"), [])

    def test_broadcast_is_rate_limited(self):
        core.send_message("alpha", "ALL", "first")
        with self.assertRaises(AgopsError) as ctx:
            core.send_message("alpha", "ALL", "second")
        self.assertIn("broadcast refused", str(ctx.exception))

    def test_handoff_carries_the_context_and_moves_ownership(self):
        core.create_task("half-done work")
        core.claim_task("TASK-001", "alpha")
        core.request_handoff("alpha", "bravo", "TASK-001",
                             state="parser works, encoder does not",
                             changed="added mavlink/parse.py",
                             remaining="encoder + round-trip test",
                             problems="CRC differs on ADSB frames",
                             files=["backend/app/mavlink/parse.py"],
                             tests="unit green, SITL not run",
                             next_action="start from the CRC table")
        msg = core.inbox("bravo")[0]
        self.assertEqual(msg["msg_type"], "HANDOFF")
        for token in ("what remains", "CRC", "parse.py", "SITL"):
            self.assertIn(token, msg["content"])
        self.assertEqual(core.get_task("TASK-001")["owner"], "bravo")

    def test_bad_message_type_refused(self):
        with self.assertRaises(AgopsError):
            core.send_message("alpha", "bravo", "hi", "GOSSIP")


# --- 10 -----------------------------------------------------------------------

class TestCrashAndRecovery(Base):
    def setUp(self):
        super().setUp()
        core.register_agent(session_id="s-a", name="alpha")
        core.register_agent(session_id="s-b", name="bravo")
        core.create_task("long running work", files=["src/x.ts"])
        core.claim_task("TASK-001", "alpha")

    def _kill_alpha(self):
        conn = core.connect()
        conn.execute("UPDATE agents SET last_heartbeat=? WHERE name='alpha'",
                     (core._now() - 99999,))
        conn.close()

    def test_session_end_preserves_task_ownership(self):
        # A closing terminal is not evidence the work is finished. Ownership
        # survives; only liveness changes.
        res = core.unregister_agent("alpha")
        self.assertEqual(res["tasks_still_owned"], ["TASK-001"])
        self.assertEqual(core.get_task("TASK-001")["owner"], "alpha")
        self.assertEqual(core.get_task("TASK-001")["status"], "IN_PROGRESS")

    def test_stale_agent_is_detected_but_not_auto_stolen(self):
        self._kill_alpha()
        self.assertEqual([a["name"] for a in core.stale_agents()], ["alpha"])
        dry = core.recover()
        self.assertTrue(dry["dry_run"])
        self.assertEqual(dry["recoverable"][0]["tasks"], ["TASK-001"])
        self.assertEqual(core.get_task("TASK-001")["status"], "IN_PROGRESS",
                         "a dry run must not change anything")

    def test_recovery_flags_for_review_and_reports_git_state(self):
        self._kill_alpha()
        res = core.recover(dry_run=False)
        self.assertIn("dirty_count", res["recoverable"][0]["git"])
        t = core.get_task("TASK-001")
        self.assertEqual(t["status"], "REVIEW")
        self.assertEqual(t["needs_recovery"], 1)
        self.assertEqual(t["owner"], "alpha", "the record of who had it survives")

    def test_reclaim_requires_stating_you_checked(self):
        self._kill_alpha()
        core.recover(dry_run=False)
        with self.assertRaises(AgopsError) as ctx:
            core.reclaim("TASK-001", "bravo")
        self.assertIn("Inspect", str(ctx.exception))
        t = core.reclaim("TASK-001", "bravo", verified=True)
        self.assertEqual(t["owner"], "bravo")
        self.assertEqual(t["status"], "IN_PROGRESS")

    def test_recovery_never_touches_files_or_git(self):
        before = subprocess.run(["git", "-C", REPO, "status", "--porcelain"],
                                capture_output=True, text=True).stdout
        self._kill_alpha()
        core.recover(dry_run=False)
        after = subprocess.run(["git", "-C", REPO, "status", "--porcelain"],
                               capture_output=True, text=True).stdout
        self.assertEqual(before, after, "recovery modified the working tree")


# --- 11 -----------------------------------------------------------------------

class TestRestart(Base):
    def test_reconnecting_session_keeps_one_identity(self):
        first = core.register_agent(session_id="s-a", name="alpha")
        core.create_task("work")
        core.claim_task("TASK-001", "alpha")
        core.unregister_agent("alpha")
        again = core.register_agent(session_id="s-a")
        self.assertFalse(again["created"], "a restart forked a second agent")
        self.assertEqual(again["agent"]["name"], first["agent"]["name"])
        self.assertEqual(len(core.list_agents()), 1)
        self.assertEqual(again["agent"]["status"], "IDLE",
                         "an OFFLINE agent that comes back should be live again")
        self.assertEqual(core.get_task("TASK-001")["owner"], "alpha")

    def test_a_different_session_gets_a_different_identity(self):
        core.register_agent(session_id="s-a", name="alpha")
        core.register_agent(session_id="s-b")
        self.assertEqual(len(core.list_agents()), 2)


# --- discovery / completion ----------------------------------------------------

class TestDiscoveryAndCompletion(Base):
    def setUp(self):
        super().setUp()
        core.register_agent(session_id="s-a", name="alpha",
                            specialties=["planner", "coverage"])
        core.register_agent(session_id="s-b", name="bravo", specialties=["ui"])

    def test_ranking_puts_priority_first(self):
        core.create_task("low thing", priority="LOW")
        core.create_task("critical thing", priority="CRITICAL")
        self.assertEqual(core.next_tasks("alpha")[0]["title"], "critical thing")

    def test_specialty_breaks_ties(self):
        core.create_task("map rendering for the ui", priority="MEDIUM")
        core.create_task("coverage planner geometry", priority="MEDIUM")
        self.assertIn("coverage", core.next_tasks("alpha")[0]["title"])
        self.assertIn("ui", core.next_tasks("bravo")[0]["title"])

    def test_specialty_is_a_signal_not_a_restriction(self):
        core.create_task("map rendering for the ui")
        self.assertTrue(core.claim_task("TASK-001", "alpha")["ok"])

    def test_conflicted_work_ranks_below_clear_work(self):
        core.create_task("clear", files=["src/free.ts"])
        core.create_task("busy a", files=["src/busy.ts"])
        core.create_task("busy b", files=["src/busy.ts"])
        core.claim_task("TASK-002", "bravo")
        self.assertEqual(core.next_tasks("alpha")[0]["title"], "clear")

    def test_cannot_complete_a_task_nobody_claimed(self):
        # Otherwise any agent can close any AVAILABLE task -- including one it
        # never opened -- and the board records it as their work.
        core.create_task("unclaimed work")
        with self.assertRaises(AgopsError) as ctx:
            core.complete_task("TASK-001", "alpha",
                               "implemented the parser and verified it in SITL")
        self.assertIn("Claim it first", str(ctx.exception))
        self.assertEqual(core.get_task("TASK-001")["status"], "AVAILABLE")

    def test_completion_requires_a_real_summary(self):
        core.create_task("work")
        core.claim_task("TASK-001", "alpha")
        for thin in ("done", "did the thing", "fixed it all up"):
            with self.assertRaises(AgopsError):
                core.complete_task("TASK-001", "alpha", thin)

    def test_completion_refuses_when_tests_fail(self):
        core.create_task("work")
        core.claim_task("TASK-001", "alpha")
        with self.assertRaises(AgopsError) as ctx:
            core.complete_task("TASK-001", "alpha",
                               "implemented the parser and its round-trip",
                               tests_passed=False)
        self.assertIn("failing", str(ctx.exception))
        self.assertEqual(core.get_task("TASK-001")["status"], "IN_PROGRESS")

    def test_completion_records_the_commit(self):
        core.create_task("work")
        core.claim_task("TASK-001", "alpha")
        core.complete_task("TASK-001", "alpha", "parser implemented and verified in SITL",
                           verification="47 unit tests", commit_hash="abc1234",
                           tests_passed=True)
        t = core.get_task("TASK-001")
        self.assertEqual(t["commit_hash"], "abc1234")
        self.assertEqual(t["completed_by"], "alpha")
        self.assertEqual(t["status"], "COMPLETE")

    def test_agent_is_freed_after_completing(self):
        core.create_task("work")
        core.claim_task("TASK-001", "alpha")
        core.complete_task("TASK-001", "alpha",
                           "finished the work and verified it end to end")
        me = [a for a in core.list_agents() if a["name"] == "alpha"][0]
        self.assertIsNone(me["current_task"])
        self.assertEqual(me["status"], "IDLE")


# --- resources, override, degradation ------------------------------------------

class TestResourcesAndOverride(Base):
    def setUp(self):
        super().setUp()
        core.register_agent(session_id="s-a", name="alpha")
        core.register_agent(session_id="s-b", name="bravo")

    def test_exclusive_resource_has_one_holder(self):
        self.assertTrue(core.take_resource("sitl-5760", "alpha")["ok"])
        second = core.take_resource("sitl-5760", "bravo")
        self.assertFalse(second["ok"])
        self.assertEqual(second["holder"], "alpha")
        core.drop_resource("sitl-5760", "alpha")
        self.assertTrue(core.take_resource("sitl-5760", "bravo")["ok"])

    def test_human_can_pause_coordination(self):
        cfg = core.load_config()
        cfg["coordination_enabled"] = False
        core.save_config(cfg)
        self.assertFalse(core.project_status()["coordination_enabled"])

    def test_events_answer_who_did_what(self):
        core.create_task("work", files=["src/x.ts"])
        core.claim_task("TASK-001", "alpha")
        core.complete_task("TASK-001", "alpha",
                           "done properly, the whole suite is green",
                           commit_hash="deadbee")
        conn = core.connect()
        try:
            kinds = [r["kind"] for r in conn.execute(
                "SELECT kind FROM events ORDER BY id")]
        finally:
            conn.close()
        for expected in ("agent.register", "task.create", "task.claim",
                         "task.complete"):
            self.assertIn(expected, kinds)


class TestGracefulDegradation(Base):
    def test_doctor_reports_health_honestly(self):
        r = run_cli("doctor", "--json")
        self.assertEqual(r.returncode, 0)
        self.assertTrue(json.loads(r.stdout)["db"])

    def test_cli_reports_a_refusal_without_crashing(self):
        core.register_agent(session_id="s-a", name="alpha")
        r = run_cli("claim", "TASK-999", "--agent", "alpha")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no such task", r.stderr)

    def test_hooks_fail_open_on_a_broken_payload(self):
        # A hook that dies must not take the session or the edit with it.
        for hook in ("hook_session_start.py", "hook_pretooluse.py",
                     "hook_stop.py", "hook_session_end.py"):
            p = subprocess.run([sys.executable, os.path.join(TOOLS, "agops", hook)],
                               input="not json at all", capture_output=True,
                               text=True, timeout=60,
                               env=dict(os.environ, AGOPS_HOME=_TMP))
            self.assertIn(p.returncode, (0,),
                          "%s did not fail open (rc=%d)" % (hook, p.returncode))


class TestMcpServer(Base):
    """The MCP surface must expose the same core and refuse the same things."""

    def _rpc(self, *requests):
        payload = "\n".join(json.dumps(r) for r in requests) + "\n"
        p = subprocess.run(
            [sys.executable, os.path.join(TOOLS, "agops", "mcp_server.py")],
            input=payload, capture_output=True, text=True, timeout=60,
            env=dict(os.environ, AGOPS_HOME=_TMP))
        return [json.loads(l) for l in p.stdout.splitlines() if l.strip()]

    def test_initialize_and_tools_list(self):
        out = self._rpc(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(out[0]["result"]["serverInfo"]["name"], "agops")
        names = {t["name"] for t in out[1]["result"]["tools"]}
        for required in ("agops_status", "agops_claim_task", "agops_complete_task",
                         "agops_send_message", "agops_check_conflicts",
                         "agops_next_tasks", "agops_recover"):
            self.assertIn(required, names)
        for t in out[1]["result"]["tools"]:
            self.assertTrue(t["description"].strip())
            self.assertEqual(t["inputSchema"]["type"], "object")

    def test_claim_through_mcp_is_the_same_state(self):
        core.register_agent(session_id="s-a", name="alpha")
        core.create_task("mcp work")
        out = self._rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                         "params": {"name": "agops_claim_task",
                                    "arguments": {"task_id": "TASK-001",
                                                  "agent": "alpha"}}})
        self.assertNotIn("error", out[0])
        self.assertEqual(core.get_task("TASK-001")["owner"], "alpha")

    def test_refusal_comes_back_as_a_readable_result(self):
        core.register_agent(session_id="s-a", name="alpha")
        out = self._rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                         "params": {"name": "agops_claim_task",
                                    "arguments": {"task_id": "TASK-404",
                                                  "agent": "alpha"}}})
        body = out[0]["result"]["content"][0]["text"]
        self.assertTrue(out[0]["result"]["isError"])
        self.assertIn("no such task", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
