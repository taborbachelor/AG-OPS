#!/usr/bin/env python
"""AgOps coordination core: agents, tasks, messages, ownership, events.

WHY THIS EXISTS. Multiple Claude Code sessions work this repo at once. Git is
source control; it is a poor communication bus (you cannot ask it who is working
on what right now, and using commits to talk means every message is a merge
conflict waiting to happen). This module is the answer to four questions Git
cannot answer:

    who is here          -> agents
    who is doing what    -> tasks
    what must I know     -> messages
    what may I edit      -> ownership (task files + areas)

DESIGN RULES, each one paid for by a real failure during the first three-session
day (see LANES.md "Field notes for a redesign"):

1.  **Identity is solved before ownership.** An agent that claims work under the
    wrong id gets locked out of its own files by its own claim. Identity comes
    from the Claude session_id in the hook payload, never from a guess.
2.  **Fail open, always.** Coordination is an aid, not a dependency. Every
    entry point here returns a structured result; callers (hooks especially)
    treat any internal error as "allow and warn". A broken coordinator must
    never stop someone from editing a file.
3.  **Atomic where it counts.** Exactly one agent can win a claim. That is a
    conditional UPDATE inside an IMMEDIATE transaction, not an agent
    remembering to check first.
4.  **Advisory by default, blocking only on direct overlap.** A guard that
    blocks legitimate work trains agents to route around the mechanism, which
    is worse than no guard.
5.  **Never destroy.** Recovery preserves a crashed agent's work and its task
    record; nothing here deletes files, resets git, or discards changes.

STORAGE. One SQLite file per project at <repo>/.agops/agops.db, WAL mode. Local,
fast, transactional, zero dependencies, and it disappears cleanly if you delete
the directory. Config that humans edit lives in <repo>/.agops/project.json and
IS tracked by git; runtime state is not.
"""

from __future__ import annotations

import fnmatch
import json
import os
import sqlite3
import subprocess
import time
import uuid

SCHEMA_VERSION = 1

# --- locations ---------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
# AGOPS_HOME relocates all coordination state. Set by the test suite so tests
# never touch the live team board, and usable by a human who wants a sandbox.
AGOPS_DIR = os.environ.get("AGOPS_HOME") or os.path.join(REPO, ".agops")
DB_PATH = os.path.join(AGOPS_DIR, "agops.db")
CONFIG_PATH = os.path.join(AGOPS_DIR, "project.json")

# --- vocabularies ------------------------------------------------------------

TASK_STATUSES = ("PENDING", "AVAILABLE", "IN_PROGRESS", "BLOCKED",
                 "REVIEW", "COMPLETE", "CANCELLED")
AGENT_STATUSES = ("STARTING", "IDLE", "WORKING", "BLOCKED", "REVIEWING",
                  "WAITING", "OFFLINE", "ERROR")
PRIORITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
PRIORITY_RANK = {p: i for i, p in enumerate(PRIORITIES)}
MESSAGE_TYPES = ("INFO", "QUESTION", "WARNING", "BLOCKER", "HANDOFF",
                 "REVIEW_REQUEST", "COMPLETION", "DISPATCH")
CONFLICT_LEVELS = ("NONE", "WARNING", "BLOCKING")

# NATO order. Deterministic so the Nth agent to ever join gets the Nth name and
# names never shuffle between runs.
NAME_POOL = ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf",
             "hotel", "india", "juliet", "kilo", "lima", "mike", "november",
             "oscar", "papa", "quebec", "romeo", "sierra", "tango", "uniform",
             "victor", "whiskey", "xray", "yankee", "zulu")

# Conservative on purpose. A quiet agent is usually thinking, not dead; stealing
# its task after a few minutes is how you get two agents on one problem.
DEFAULT_STALE_S = 45 * 60
# Heartbeats older than this mean the agent is gone rather than merely quiet.
DEFAULT_OFFLINE_S = 4 * 60 * 60


class AgopsError(Exception):
    """A refusal the caller should surface verbatim (not an internal fault)."""


# --- config ------------------------------------------------------------------

def default_config() -> dict:
    return {
        "project_id": "agops-gcs",
        "project_name": "AgOps GCS",
        "description": "Autonomous agricultural drone ground control station",
        "schema_version": SCHEMA_VERSION,
        "coordination_enabled": True,
        "enforcement": "advisory",
        # WHO DECIDES WHAT AN AGENT WORKS ON.
        #   "assigned"   the human dispatches; agents never take work themselves.
        #                They can read the board and recommend, and that is all.
        #   "on_request" agents may claim, but only when told to in the moment.
        #   "self_serve" agents pull from the queue freely (swarm).
        # Default "assigned", because the problem this system was built for is
        # three sessions landing on one task -- not idle agents. Dispatch is the
        # thing a human is actually good at and wants to keep.
        "claim_policy": "assigned",
        # Whether an idle agent may claim work on its own initiative.
        # OFF by default, and that default is deliberate: a session that was
        # asked a status question once claimed a task and shipped a feature.
        # Proposing costs a sentence; claiming commits an agent, a file lock and
        # a commit. With this false, agents surface ranked candidates and WAIT
        # for the human to say continue. Set true for unattended swarm runs.
        "auto_claim": False,
        "stale_after_s": DEFAULT_STALE_S,
        "offline_after_s": DEFAULT_OFFLINE_S,
        # A NEW session registering while nobody is live marks the start of a
        # fresh work session: offline agents holding no work are pruned first,
        # so the day's sessions start over at alpha instead of marching through
        # the NATO alphabet (three launch batches burned kilo through sierra in
        # one morning). Agents that still hold work keep their names -- the
        # same guard `admin clear-agents` has.
        "recycle_names_on_fresh_start": True,
        # Standby: after COMPLETING a task, an agent whose lead is on duty
        # waits for its next dispatch (`await-dispatch`) instead of going cold
        # -- this is what lets a manager session chain agents through a wave
        # without a human typing "continue" in each terminal. Bounded on
        # purpose: standby_cycles empty waits (each standby_wait_s long) and
        # the agent stops for real, telling the lead via agent.standby_expired.
        # Scoped on purpose: ONLY after a recent completion, so a session
        # merely chatting with the human is never hijacked into a silent wait.
        # standby_cycles 0 turns the whole mechanism off.
        "standby_cycles": 3,
        "standby_wait_s": 540,
        "standby_recent_complete_s": 900,
        # The only repo paths a role=lead session may write. Coordination
        # state, nothing else: the lead dispatches and verifies, it does not
        # edit the product. Enforced in the PreToolUse guard.
        "lead_writable": [".agops/*", ".agops/**"],
        "broadcast_min_interval_s": 300,
        "always_open": ["LANES.md", ".agops/*", ".agops/**", ".claim/*", "*.log"],
        "areas": {},
        "resources": {},
        "known_agents": {},
    }


def load_config() -> dict:
    cfg = default_config()
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            cfg.update(json.load(fh))
    except (OSError, ValueError):
        pass                      # rule 2: missing config is not a failure
    return cfg


def save_config(cfg: dict) -> None:
    os.makedirs(AGOPS_DIR, exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, CONFIG_PATH)


def project_id() -> str:
    return load_config().get("project_id", "agops-gcs")


def project_fingerprint() -> dict:
    """Identity of the repo this process is actually standing in.

    Used to refuse coordination calls aimed at another project. The DB already
    lives inside the repo, so this is the second of two independent barriers --
    a session in another checkout cannot reach this file at all, and a session
    passing the wrong project_id is rejected on sight.
    """
    top = REPO
    try:
        out = subprocess.run(["git", "-C", REPO, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            top = os.path.abspath(out.stdout.strip())
    except Exception:
        pass
    return {"project_id": project_id(), "repo_root": top}


def require_project(pid) -> None:
    if pid is None:
        return
    mine = project_id()
    if pid != mine:
        raise AgopsError(
            "project mismatch: this repository is '%s', not '%s'. "
            "AgOps state is per-repository; a session in another project "
            "cannot join this team." % (mine, pid))


# --- database ----------------------------------------------------------------

DDL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS agents (
    agent_id       TEXT PRIMARY KEY,
    name           TEXT UNIQUE NOT NULL,
    project_id     TEXT NOT NULL,
    session_id     TEXT,
    cwd            TEXT,
    branch         TEXT,
    worktree       TEXT,
    role           TEXT DEFAULT 'worker',
    specialties    TEXT DEFAULT '[]',
    status         TEXT DEFAULT 'STARTING',
    current_task   TEXT,
    note           TEXT,
    pid            INTEGER,
    started_at     REAL,
    last_heartbeat REAL
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id             TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL,
    title               TEXT NOT NULL,
    description         TEXT DEFAULT '',
    status              TEXT NOT NULL,
    priority            TEXT DEFAULT 'MEDIUM',
    owner               TEXT,
    area                TEXT,
    estimate            TEXT,
    blocked_reason      TEXT,
    created_by          TEXT,
    completed_by        TEXT,
    completion_summary  TEXT,
    verification_status TEXT,
    commit_hash         TEXT,
    needs_recovery      INTEGER DEFAULT 0,
    recovery_note       TEXT,
    created_at          REAL,
    updated_at          REAL,
    claimed_at          REAL,
    completed_at        REAL
);

CREATE TABLE IF NOT EXISTS task_deps (
    task_id    TEXT NOT NULL,
    depends_on TEXT NOT NULL,
    PRIMARY KEY (task_id, depends_on)
);

CREATE TABLE IF NOT EXISTS task_files (
    task_id TEXT NOT NULL,
    path    TEXT NOT NULL,
    PRIMARY KEY (task_id, path)
);

CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    TEXT NOT NULL,
    sender        TEXT NOT NULL,
    recipient     TEXT NOT NULL,
    msg_type      TEXT DEFAULT 'INFO',
    content       TEXT NOT NULL,
    related_task  TEXT,
    related_files TEXT,
    created_at    REAL,
    read_at       REAL
);

-- Read state is PER RECIPIENT, not per message. A broadcast is one row with
-- many readers; storing read_at on the message meant the first agent to read a
-- broadcast marked it read for the entire team, and everyone else silently
-- never saw it. Found by test_broadcast_reaches_everyone_but_the_sender.
CREATE TABLE IF NOT EXISTS message_reads (
    message_id INTEGER NOT NULL,
    agent      TEXT NOT NULL,
    read_at    REAL,
    PRIMARY KEY (message_id, agent)
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    ts         REAL,
    actor      TEXT,
    kind       TEXT,
    subject    TEXT,
    detail     TEXT
);

CREATE TABLE IF NOT EXISTS resources (
    name     TEXT PRIMARY KEY,
    holder   TEXT,
    taken_at REAL,
    reason   TEXT
);

CREATE TABLE IF NOT EXISTS resource_waits (
    resource TEXT NOT NULL,
    agent    TEXT NOT NULL,
    since    REAL NOT NULL,
    note     TEXT,
    PRIMARY KEY (resource, agent)
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_msg_recipient ON messages(recipient);
CREATE INDEX IF NOT EXISTS idx_msg_reads ON message_reads(agent, message_id);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
"""


def connect() -> sqlite3.Connection:
    os.makedirs(AGOPS_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(DDL)
    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('schema', ?)",
                 (str(SCHEMA_VERSION),))
    return conn


def _now() -> float:
    return time.time()


def _event(conn, actor, kind, subject="", detail="") -> None:
    conn.execute(
        "INSERT INTO events(project_id, ts, actor, kind, subject, detail) "
        "VALUES(?,?,?,?,?,?)",
        (project_id(), _now(), actor, kind, subject,
         detail if isinstance(detail, str) else json.dumps(detail)))


def _row(r) -> dict:
    return dict(r) if r is not None else None


# --- agents ------------------------------------------------------------------

def _git_branch() -> str:
    try:
        out = subprocess.run(["git", "-C", REPO, "rev-parse", "--abbrev-ref", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def _assign_name(conn, preferred=None) -> str:
    """Pick a free NATO name, honouring an explicit preference when possible."""
    taken = {r["name"] for r in conn.execute("SELECT name FROM agents")}
    if preferred:
        preferred = preferred.strip().lower()
        if preferred not in taken:
            return preferred
    for n in NAME_POOL:
        if n not in taken:
            return n
    i = len(taken) + 1
    while "agent-%d" % i in taken:
        i += 1
    return "agent-%d" % i


def _recycle_roster(conn, cfg) -> list:
    """Free the NATO names when a fresh work session begins.

    Runs only while registering a session the roster has never seen, inside
    that registration's transaction. If anyone is live -- non-OFFLINE with a
    heartbeat inside the stale window -- this is a mid-session arrival and
    does nothing. When nobody is live, the arriving session is the start of a
    fresh work session, and every agent holding no work is pruned so names
    start over at alpha.

    Agents still holding work are KEPT, name and all -- the same guard
    `admin clear-agents` has: an owned task on a dead session is either live
    work or a crash worth recovering, and forgetting who had it loses both.
    A pruned agent's inbound mail is marked read under its old name first, so
    the next session to wear the name does not inherit a ghost's unread
    count, and any resource a pruned ghost still held is freed the same way
    session-end frees them.
    """
    if not cfg.get("recycle_names_on_fresh_start", True):
        return []
    now = _now()
    stale_after = cfg.get("stale_after_s", DEFAULT_STALE_S)
    if conn.execute("SELECT 1 FROM agents WHERE status<>'OFFLINE' "
                    "AND last_heartbeat>=? LIMIT 1",
                    (now - stale_after,)).fetchone():
        return []
    keep = {r["name"] for r in conn.execute(
        "SELECT name FROM agents WHERE current_task IS NOT NULL")}
    keep |= {r["owner"] for r in conn.execute(
        "SELECT DISTINCT owner FROM tasks WHERE owner IS NOT NULL AND "
        "(status='IN_PROGRESS' OR needs_recovery=1)")}
    gone = [r["name"] for r in conn.execute("SELECT name FROM agents")
            if r["name"] not in keep]
    for n in gone:
        conn.execute(
            "INSERT OR IGNORE INTO message_reads(message_id, agent, read_at) "
            "SELECT id, ?, ? FROM messages WHERE recipient=? OR recipient='ALL'",
            (n, now, n))
        conn.execute("DELETE FROM resource_waits WHERE agent=?", (n,))
        conn.execute("UPDATE resources SET holder=NULL WHERE holder=?", (n,))
        conn.execute("DELETE FROM agents WHERE name=?", (n,))
    if gone:
        _event(conn, "system", "team.recycle", "",
               {"freed": gone, "kept": sorted(keep)})
    return gone


def register_agent(session_id=None, name=None, specialties=None, role=None,
                   cwd=None, pid_=None, worktree=None, project=None) -> dict:
    """Register or re-attach a session. Idempotent per session_id.

    Re-running this for a session that already exists updates its liveness and
    returns the SAME identity -- restarting a session must never fork a second
    agent, because the duplicate would then contend with its own claims.
    """
    require_project(project)
    conn = connect()
    try:
        agent_id = session_id or ("anon-" + uuid.uuid4().hex[:12])
        existing = conn.execute("SELECT * FROM agents WHERE agent_id=?",
                                (agent_id,)).fetchone()
        cfg = load_config()
        known = {v: k for k, v in cfg.get("known_agents", {}).items()}
        now = _now()
        if existing:
            conn.execute(
                "UPDATE agents SET last_heartbeat=?, cwd=COALESCE(?,cwd), "
                "branch=?, worktree=COALESCE(?,worktree), pid=COALESCE(?,pid), "
                "status=CASE WHEN status='OFFLINE' THEN 'IDLE' ELSE status END "
                "WHERE agent_id=?",
                (now, cwd, _git_branch(), worktree, pid_, agent_id))
            if specialties:
                conn.execute("UPDATE agents SET specialties=? WHERE agent_id=?",
                             (json.dumps(specialties), agent_id))
            if role:
                conn.execute("UPDATE agents SET role=? WHERE agent_id=?",
                             (role, agent_id))
            _event(conn, existing["name"], "agent.reattach", existing["name"])
            return {"created": False,
                    "agent": _row(conn.execute("SELECT * FROM agents WHERE agent_id=?",
                                               (agent_id,)).fetchone())}
        # BEGIN IMMEDIATE so recycle + name pick + insert are one atomic step:
        # two first-of-the-day launches serialize here instead of both pruning
        # an all-offline roster and both choosing "alpha".
        conn.execute("BEGIN IMMEDIATE")
        recycled = _recycle_roster(conn, cfg)
        chosen = _assign_name(conn, name or known.get(agent_id))
        conn.execute(
            "INSERT INTO agents(agent_id, name, project_id, session_id, cwd, "
            "branch, worktree, role, specialties, status, started_at, "
            "last_heartbeat, pid) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (agent_id, chosen, project_id(), session_id, cwd or REPO,
             _git_branch(), worktree, role or "worker",
             json.dumps(specialties or cfg.get("specialties", {}).get(chosen, [])),
             "STARTING", now, now, pid_))
        _event(conn, chosen, "agent.register", chosen,
               {"session_id": session_id, "cwd": cwd})
        conn.execute("COMMIT")
        return {"created": True, "recycled": recycled,
                "agent": _row(conn.execute("SELECT * FROM agents WHERE agent_id=?",
                                           (agent_id,)).fetchone())}
    finally:
        conn.close()


def _resolve_agent(conn, who: str):
    """Accept an agent name or an agent_id/session_id. Names are what humans use."""
    if not who:
        return None
    r = conn.execute("SELECT * FROM agents WHERE name=?", (who.strip().lower(),)).fetchone()
    if r:
        return r
    return conn.execute("SELECT * FROM agents WHERE agent_id=? OR session_id=?",
                        (who, who)).fetchone()


def heartbeat(who, status=None, note=None, project=None) -> dict:
    require_project(project)
    conn = connect()
    try:
        a = _resolve_agent(conn, who)
        if a is None:
            raise AgopsError("unknown agent %r -- register first" % who)
        if status and status not in AGENT_STATUSES:
            raise AgopsError("bad status %r (use one of %s)"
                             % (status, ", ".join(AGENT_STATUSES)))
        conn.execute("UPDATE agents SET last_heartbeat=?, status=COALESCE(?,status), "
                     "note=COALESCE(?,note) WHERE agent_id=?",
                     (_now(), status, note, a["agent_id"]))
        return _row(conn.execute("SELECT * FROM agents WHERE agent_id=?",
                                 (a["agent_id"],)).fetchone())
    finally:
        conn.close()


def unregister_agent(who, project=None) -> dict:
    """Mark an agent OFFLINE. Its task ownership is DELIBERATELY preserved.

    A session ending is not evidence the work is abandoned -- the terminal may
    have closed mid-edit. Ownership is released only by an explicit release, or
    by recovery after the stale timeout, so nobody picks up half-finished work
    without a human or a deliberate recovery step looking at it first.
    """
    require_project(project)
    conn = connect()
    try:
        a = _resolve_agent(conn, who)
        if a is None:
            return {"ok": False, "reason": "unknown agent"}
        conn.execute("UPDATE agents SET status='OFFLINE', last_heartbeat=? "
                     "WHERE agent_id=?", (_now(), a["agent_id"]))
        held = [r["task_id"] for r in conn.execute(
            "SELECT task_id FROM tasks WHERE owner=? AND status='IN_PROGRESS'",
            (a["name"],))]
        _event(conn, a["name"], "agent.offline", a["name"], {"held": held})
        return {"ok": True, "agent": a["name"], "tasks_still_owned": held}
    finally:
        conn.close()


def list_agents(include_offline=True, project=None) -> list:
    require_project(project)
    conn = connect()
    try:
        cfg = load_config()
        stale_after = cfg.get("stale_after_s", DEFAULT_STALE_S)
        offline_after = cfg.get("offline_after_s", DEFAULT_OFFLINE_S)
        now = _now()
        out = []
        for r in conn.execute("SELECT * FROM agents ORDER BY started_at"):
            d = _row(r)
            quiet = now - (d.get("last_heartbeat") or 0)
            d["quiet_s"] = int(quiet)
            d["stale"] = quiet > stale_after and d["status"] != "OFFLINE"
            d["presumed_gone"] = quiet > offline_after
            d["specialties"] = json.loads(d.get("specialties") or "[]")
            if not include_offline and d["status"] == "OFFLINE":
                continue
            out.append(d)
        return out
    finally:
        conn.close()


# --- tasks -------------------------------------------------------------------

def _next_task_id(conn) -> str:
    row = conn.execute("SELECT task_id FROM tasks ORDER BY task_id DESC LIMIT 1").fetchone()
    n = 0
    if row:
        try:
            n = int(str(row["task_id"]).split("-")[-1])
        except ValueError:
            n = 0
    return "TASK-%03d" % (n + 1)


def _recompute_availability(conn, task_ids=None) -> list:
    """Flip PENDING/BLOCKED tasks to AVAILABLE once every prerequisite is COMPLETE.

    Dependency state is derived, never hand-maintained: an agent completing a
    task should not also have to remember which other tasks that unblocks.
    """
    changed = []
    if task_ids is None:
        rows = conn.execute(
            "SELECT task_id FROM tasks WHERE status IN ('PENDING','BLOCKED')").fetchall()
        task_ids = [r["task_id"] for r in rows]
    for tid in task_ids:
        t = conn.execute("SELECT * FROM tasks WHERE task_id=?", (tid,)).fetchone()
        if t is None or t["status"] not in ("PENDING", "BLOCKED"):
            continue
        # A manual block (a reason with no unmet dependency) is a human/agent
        # judgement; dependency recomputation must not silently override it.
        deps = [r["depends_on"] for r in conn.execute(
            "SELECT depends_on FROM task_deps WHERE task_id=?", (tid,))]
        unmet = []
        for d in deps:
            dt = conn.execute("SELECT status FROM tasks WHERE task_id=?", (d,)).fetchone()
            if dt is None or dt["status"] != "COMPLETE":
                unmet.append(d)
        if unmet:
            if t["status"] != "BLOCKED":
                conn.execute("UPDATE tasks SET status='BLOCKED', blocked_reason=?, "
                             "updated_at=? WHERE task_id=?",
                             ("waiting for " + ", ".join(unmet), _now(), tid))
                changed.append((tid, "BLOCKED"))
            continue
        if t["status"] == "BLOCKED" and t["blocked_reason"] and \
                not t["blocked_reason"].startswith("waiting for"):
            continue                      # manual block stands
        conn.execute("UPDATE tasks SET status='AVAILABLE', blocked_reason=NULL, "
                     "updated_at=? WHERE task_id=?", (_now(), tid))
        changed.append((tid, "AVAILABLE"))
        _event(conn, "system", "task.available", tid, "dependencies satisfied")
    return changed


def create_task(title, description="", priority="MEDIUM", depends_on=None,
                files=None, area=None, created_by="human", estimate=None,
                task_id=None, project=None) -> dict:
    require_project(project)
    if priority not in PRIORITIES:
        raise AgopsError("bad priority %r (use %s)" % (priority, ", ".join(PRIORITIES)))
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        tid = task_id or _next_task_id(conn)
        if conn.execute("SELECT 1 FROM tasks WHERE task_id=?", (tid,)).fetchone():
            conn.execute("ROLLBACK")
            raise AgopsError("%s already exists" % tid)
        now = _now()
        conn.execute(
            "INSERT INTO tasks(task_id, project_id, title, description, status, "
            "priority, area, estimate, created_by, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (tid, project_id(), title, description, "PENDING", priority, area,
             estimate, created_by, now, now))
        for d in (depends_on or []):
            conn.execute("INSERT OR IGNORE INTO task_deps(task_id, depends_on) "
                         "VALUES(?,?)", (tid, d))
        for f in (files or []):
            conn.execute("INSERT OR IGNORE INTO task_files(task_id, path) "
                         "VALUES(?,?)", (tid, f.replace("\\", "/")))
        _event(conn, created_by, "task.create", tid, title)
        conn.execute("COMMIT")
        _recompute_availability(conn, [tid])
        return get_task(tid)
    finally:
        conn.close()


def get_task(tid, project=None) -> dict:
    require_project(project)
    conn = connect()
    try:
        r = conn.execute("SELECT * FROM tasks WHERE task_id=?", (tid,)).fetchone()
        if r is None:
            raise AgopsError("no such task %r" % tid)
        d = _row(r)
        d["dependencies"] = [x["depends_on"] for x in conn.execute(
            "SELECT depends_on FROM task_deps WHERE task_id=?", (tid,))]
        d["dependents"] = [x["task_id"] for x in conn.execute(
            "SELECT task_id FROM task_deps WHERE depends_on=?", (tid,))]
        d["affected_files"] = [x["path"] for x in conn.execute(
            "SELECT path FROM task_files WHERE task_id=?", (tid,))]
        return d
    finally:
        conn.close()


def list_tasks(status=None, owner=None, project=None) -> list:
    require_project(project)
    conn = connect()
    try:
        q, args = "SELECT task_id FROM tasks WHERE 1=1", []
        if status:
            marks = ",".join("?" * len(status)) if isinstance(status, (list, tuple)) else "?"
            q += " AND status IN (%s)" % marks
            args.extend(status if isinstance(status, (list, tuple)) else [status])
        if owner:
            q += " AND owner=?"
            args.append(owner)
        ids = [r["task_id"] for r in conn.execute(q, args)]
    finally:
        conn.close()
    return [get_task(t) for t in ids]


# --- ownership / conflicts ---------------------------------------------------

def _match(path: str, pattern: str) -> bool:
    p = path.replace("\\", "/").lstrip("./")
    g = pattern.replace("\\", "/").lstrip("./")
    if fnmatch.fnmatch(p, g):
        return True
    if g.endswith("/**") and p.startswith(g[:-3] + "/"):
        return True
    if g.endswith("/*") and p.startswith(g[:-2] + "/"):
        return True
    if g.endswith("/") and p.startswith(g):
        return True
    return p == g


def _patterns_overlap(a: str, b: str) -> bool:
    """Do two path patterns describe any common file?

    Exact string match, either matching the other as a glob, or a shared
    directory prefix. Deliberately cheap and slightly over-eager: a false
    WARNING costs a sentence of explanation, a missed overlap costs a conflict.
    """
    if _match(a, b) or _match(b, a):
        return True
    da, db = a.rsplit("/", 1)[0], b.rsplit("/", 1)[0]
    return bool(da and da == db and ("*" in a or "*" in b))


def area_of(path: str, cfg=None) -> str:
    cfg = cfg or load_config()
    for name, spec in (cfg.get("areas") or {}).items():
        for g in spec.get("globs", []):
            if _match(path, g):
                return name
    return ""


def check_conflicts(files, agent=None, task_id=None, project=None) -> dict:
    """Who else is currently working the files this work would touch.

    Severity model:
      BLOCKING  a live agent's IN_PROGRESS task names an overlapping path
      WARNING   the path sits in an area another live agent is working in
      NONE      nobody else is near it

    Directory-level ownership is advisory (WARNING) and always loses to a
    file-level hit, so broad area claims never freeze fine-grained parallel work.
    """
    require_project(project)
    files = [f.replace("\\", "/") for f in (files or [])]
    cfg = load_config()
    conn = connect()
    try:
        stale_after = cfg.get("stale_after_s", DEFAULT_STALE_S)
        now = _now()
        live = {}
        for r in conn.execute("SELECT * FROM agents"):
            quiet = now - (r["last_heartbeat"] or 0)
            if r["status"] != "OFFLINE" and quiet <= stale_after:
                live[r["name"]] = dict(r)

        findings = []
        for r in conn.execute(
                "SELECT t.task_id, t.owner, t.title, f.path FROM tasks t "
                "JOIN task_files f ON f.task_id=t.task_id "
                "WHERE t.status='IN_PROGRESS'"):
            if task_id and r["task_id"] == task_id:
                continue
            if agent and r["owner"] == agent:
                continue
            if r["owner"] not in live:
                continue          # a stale owner does not block new work
            for f in files:
                if _patterns_overlap(f, r["path"]):
                    findings.append({
                        "level": "BLOCKING", "path": f, "their_path": r["path"],
                        "owner": r["owner"], "task_id": r["task_id"],
                        "title": r["title"],
                        "why": "%s is editing an overlapping path under %s"
                               % (r["owner"], r["task_id"])})

        if not findings:
            busy_areas = {}
            for r in conn.execute(
                    "SELECT task_id, owner, area, title FROM tasks "
                    "WHERE status='IN_PROGRESS' AND area IS NOT NULL"):
                if agent and r["owner"] == agent:
                    continue
                if r["owner"] in live:
                    busy_areas[r["area"]] = dict(r)
            for f in files:
                a = area_of(f, cfg)
                if a and a in busy_areas:
                    t = busy_areas[a]
                    findings.append({
                        "level": "WARNING", "path": f, "their_path": a,
                        "owner": t["owner"], "task_id": t["task_id"],
                        "title": t["title"],
                        "why": "%s is working in area %s (advisory)" % (t["owner"], a)})

        level = "NONE"
        if any(f["level"] == "BLOCKING" for f in findings):
            level = "BLOCKING"
        elif findings:
            level = "WARNING"
        return {"level": level, "conflicts": findings}
    finally:
        conn.close()


def get_file_owners(path, project=None) -> dict:
    require_project(project)
    res = check_conflicts([path], project=project)
    owners = sorted({c["owner"] for c in res["conflicts"]})
    return {"path": path, "area": area_of(path), "owners": owners,
            "level": res["level"], "detail": res["conflicts"]}


# --- claiming ----------------------------------------------------------------

def assign_task(tid, agent, by="human", note="", project=None) -> dict:
    """Human dispatch: give a task to a named agent and tell them.

    The counterpart to claiming. Claiming is an agent deciding; assigning is a
    person deciding, which is the mode most small teams actually want -- the
    coordination problem worth solving is two sessions on one task, and dispatch
    solves that without giving up control of what gets built next.
    """
    require_project(project)
    conn = connect()
    try:
        a = _resolve_agent(conn, agent)
        if a is None:
            raise AgopsError("no agent named %r -- see: py tools\\agops.py agents"
                             % agent)
        if (a["role"] or "") == "lead":
            raise AgopsError(
                "%s has role 'lead' -- the lead coordinates and verifies, it "
                "does not take tasks. Dispatch to a worker instead." % a["name"])
        t = conn.execute("SELECT * FROM tasks WHERE task_id=?", (tid,)).fetchone()
        if t is None:
            raise AgopsError("no such task %r" % tid)
        if t["status"] in ("COMPLETE", "CANCELLED"):
            raise AgopsError("%s is %s" % (tid, t["status"]))
        if t["owner"] and t["owner"] != a["name"]:
            raise AgopsError(
                "%s is already owned by %s. Reassigning takes it away mid-flight "
                "-- release it first if that is what you want: "
                "py tools\\agops.py admin release --task-id %s" % (tid, t["owner"], tid))

        files = [r["path"] for r in conn.execute(
            "SELECT path FROM task_files WHERE task_id=?", (tid,))]
        conflicts = check_conflicts(files, agent=a["name"], task_id=tid) if files \
            else {"level": "NONE", "conflicts": []}

        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE tasks SET owner=?, status='IN_PROGRESS', claimed_at=?, "
                     "updated_at=? WHERE task_id=?",
                     (a["name"], _now(), _now(), tid))
        conn.execute("UPDATE agents SET current_task=?, status='WORKING' "
                     "WHERE agent_id=?", (tid, a["agent_id"]))
        _event(conn, by, "task.assign", tid, "to " + a["name"])
        conn.execute("COMMIT")
    finally:
        conn.close()

    body = "You have been assigned %s: %s" % (tid, t["title"])
    if note:
        body += "\n" + note
    body += ("\nRead it with: py tools\\agops.py task %s" % tid)
    try:
        # DISPATCH, not INFO: the Stop hook treats an unread DISPATCH as
        # "do not stop -- proceed with this task", which is how an assignment
        # reaches a session without a human relaying it.
        send_message(by, a["name"], body, "DISPATCH", tid, files, project=project)
    except AgopsError:
        pass                    # a message failure must not undo the assignment
    return {"ok": True, "task_id": tid, "owner": a["name"],
            "conflicts": conflicts["conflicts"]}


def claim_task(tid, agent, force=False, project=None) -> dict:
    """Atomically take ownership. Exactly one caller can win.

    The whole guarantee is the WHERE clause: owner IS NULL AND status='AVAILABLE'.
    Two agents racing both run the same conditional UPDATE inside an IMMEDIATE
    transaction; SQLite serialises them and the second one updates zero rows and
    is told who won. There is no window in which both see it as free, because
    neither ever reads-then-writes.
    """
    require_project(project)
    conn = connect()
    try:
        a = _resolve_agent(conn, agent)
        if a is None:
            raise AgopsError("unknown agent %r -- register first" % agent)
        name = a["name"]
        if (a["role"] or "") == "lead":
            raise AgopsError(
                "you are the lead: you coordinate and verify, you do not take "
                "tasks. Dispatch it to a worker (py tools\\agops.py assign %s "
                "<agent>) or escalate to the human." % tid)
        t = conn.execute("SELECT * FROM tasks WHERE task_id=?", (tid,)).fetchone()
        if t is None:
            raise AgopsError("no such task %r" % tid)
        # Under the default policy an agent does not decide what it works on.
        # force=True is the human saying so in the moment, which is why the
        # message names the two ways forward rather than just refusing.
        policy = load_config().get("claim_policy", "assigned")
        if policy == "assigned" and not force and t["owner"] != name:
            raise AgopsError(
                "claim_policy is 'assigned': agents do not pick their own work "
                "here. Ask the human to dispatch it "
                "(py tools\\agops.py assign %s %s), or they can tell you to take "
                "it now (claim --force). You can still read the board and "
                "recommend." % (tid, name))
        if t["status"] == "BLOCKED":
            raise AgopsError("%s is BLOCKED (%s)" % (tid, t["blocked_reason"] or "?"))
        if t["status"] in ("COMPLETE", "CANCELLED"):
            raise AgopsError("%s is %s" % (tid, t["status"]))

        files = [r["path"] for r in conn.execute(
            "SELECT path FROM task_files WHERE task_id=?", (tid,))]
        conflicts = check_conflicts(files, agent=name, task_id=tid) if files else \
            {"level": "NONE", "conflicts": []}
        if conflicts["level"] == "BLOCKING" and not force:
            return {"ok": False, "reason": "conflict", "task_id": tid,
                    "conflicts": conflicts["conflicts"],
                    "message": "%s overlaps files owned by %s. Coordinate first, "
                               "or claim with force after agreeing."
                               % (tid, ", ".join(sorted(
                                   {c["owner"] for c in conflicts["conflicts"]})))}

        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "UPDATE tasks SET owner=?, status='IN_PROGRESS', claimed_at=?, "
            "updated_at=? WHERE task_id=? AND owner IS NULL AND status='AVAILABLE'",
            (name, _now(), _now(), tid))
        won = cur.rowcount == 1
        if won:
            conn.execute("UPDATE agents SET current_task=?, status='WORKING', "
                         "last_heartbeat=? WHERE agent_id=?",
                         (tid, _now(), a["agent_id"]))
            _event(conn, name, "task.claim", tid, t["title"])
        conn.execute("COMMIT")

        if not won:
            cur_t = conn.execute("SELECT owner, status FROM tasks WHERE task_id=?",
                                 (tid,)).fetchone()
            if cur_t["owner"] == name:
                return {"ok": True, "task_id": tid, "owner": name,
                        "note": "already yours", "conflicts": conflicts["conflicts"]}
            return {"ok": False, "reason": "taken", "task_id": tid,
                    "owner": cur_t["owner"], "status": cur_t["status"],
                    "message": "%s is already owned by %s." % (tid, cur_t["owner"])}
        return {"ok": True, "task_id": tid, "owner": name,
                "warnings": [c for c in conflicts["conflicts"] if c["level"] == "WARNING"]}
    finally:
        conn.close()


def release_task(tid, agent, reason="", project=None) -> dict:
    require_project(project)
    conn = connect()
    try:
        a = _resolve_agent(conn, agent)
        name = a["name"] if a else agent
        t = conn.execute("SELECT * FROM tasks WHERE task_id=?", (tid,)).fetchone()
        if t is None:
            raise AgopsError("no such task %r" % tid)
        if t["owner"] != name:
            raise AgopsError("%s is owned by %s, not %s" % (tid, t["owner"], name))
        conn.execute("UPDATE tasks SET owner=NULL, status='AVAILABLE', "
                     "updated_at=? WHERE task_id=?", (_now(), tid))
        conn.execute("UPDATE agents SET current_task=NULL, status='IDLE' "
                     "WHERE name=?", (name,))
        _event(conn, name, "task.release", tid, reason)
        return {"ok": True, "task_id": tid}
    finally:
        conn.close()


def block_task(tid, agent, reason, project=None) -> dict:
    require_project(project)
    if not reason:
        raise AgopsError("a block needs a reason")
    conn = connect()
    try:
        conn.execute("UPDATE tasks SET status='BLOCKED', blocked_reason=?, "
                     "updated_at=? WHERE task_id=?", (reason, _now(), tid))
        _event(conn, agent, "task.block", tid, reason)
        return get_task(tid)
    finally:
        conn.close()


def unblock_task(tid, agent, project=None) -> dict:
    require_project(project)
    conn = connect()
    try:
        conn.execute("UPDATE tasks SET blocked_reason=NULL, status='PENDING', "
                     "updated_at=? WHERE task_id=?", (_now(), tid))
        _recompute_availability(conn, [tid])
        _event(conn, agent, "task.unblock", tid)
        return get_task(tid)
    finally:
        conn.close()


def complete_task(tid, agent, summary, verification="", commit_hash="",
                  tests_passed=None, no_commit_reason="", project=None) -> dict:
    """Mark COMPLETE and unblock whatever was waiting on it.

    Completion demands a summary, a commit, and refuses outright when the caller
    reports failing tests. An agent that believes it is done is not evidence that
    it is; the record has to carry what was verified so the next agent can trust
    it.

    The commit requirement is the newest of the three. TASK-005 sat on the board
    as COMPLETE with "no commit recorded" for most of a day -- the exe was
    reportedly rebuilt and nothing could say whether the binary existed or what
    was in it. COMPLETE has to mean something a later session can go and look at.
    Work that genuinely produces no commit passes `no_commit_reason` and the
    board shows that instead, which is a claim someone can disagree with rather
    than a silent blank.
    """
    require_project(project)
    # Order matters: failing tests are the more urgent refusal, and hearing
    # "your summary is too short" when the real problem is a red suite would
    # send an agent off polishing the wrong thing.
    if tests_passed is False:
        raise AgopsError("tests are failing -- a task with failing tests stays "
                         "IN_PROGRESS or becomes BLOCKED. Do not report success.")
    # A crude floor, and deliberately crude: it only has to stop "done" and
    # "fixed it", not judge prose. Anything that names what changed and what was
    # verified clears five words comfortably.
    if len((summary or "").strip()) < 20 or len((summary or "").split()) < 5:
        raise AgopsError(
            "completion needs a real summary: what changed and what was "
            "verified. The next agent reads this instead of your diff.")
    conn = connect()
    try:
        t = conn.execute("SELECT * FROM tasks WHERE task_id=?", (tid,)).fetchone()
        if t is None:
            raise AgopsError("no such task %r" % tid)
        a = _resolve_agent(conn, agent)
        name = a["name"] if a else agent
        if t["owner"] and t["owner"] != name:
            raise AgopsError("%s is owned by %s, not %s" % (tid, t["owner"], name))
        # Completion is only meaningful for work somebody actually took. Without
        # this, any agent could close any AVAILABLE task -- including one it had
        # never looked at -- and the board would record it as done by them.
        if t["status"] not in ("IN_PROGRESS", "REVIEW"):
            raise AgopsError(
                "%s is %s, not yours to complete. Claim it first "
                "(py tools\\agops.py claim %s)." % (tid, t["status"], tid))
        # Last of the refusals, deliberately: an agent completing a task it does
        # not own needs to hear that, not a lecture about commit hashes.
        if not (commit_hash or "").strip() and not (no_commit_reason or "").strip():
            raise AgopsError(
                "completion needs a commit: --commit <sha>. COMPLETE is a claim "
                "the next session acts on, and one that points at nothing cannot "
                "be checked -- TASK-005 sat COMPLETE with no commit for a day and "
                "nobody could tell whether the exe existed or what was in it. If "
                "this genuinely produced no commit, say why: "
                "--no-commit-reason \"...\".")
        conn.execute(
            "UPDATE tasks SET status='COMPLETE', completed_by=?, completion_summary=?, "
            "verification_status=?, commit_hash=?, completed_at=?, updated_at=?, "
            "needs_recovery=0 WHERE task_id=?",
            (name, summary,
             verification or ("tests passed" if tests_passed else "")
             or ("no commit: " + no_commit_reason.strip()
                 if no_commit_reason.strip() else ""),
             commit_hash, _now(), _now(), tid))
        if a:
            # note carries "editing <file>" while working; a completed task's
            # last touch is stale information, so it clears here.
            conn.execute("UPDATE agents SET current_task=NULL, status='IDLE', "
                         "note=NULL, last_heartbeat=? WHERE agent_id=?",
                         (_now(), a["agent_id"]))
        _event(conn, name, "task.complete", tid, summary[:200])
        dependents = [r["task_id"] for r in conn.execute(
            "SELECT task_id FROM task_deps WHERE depends_on=?", (tid,))]
        changed = _recompute_availability(conn, dependents)
        unblocked = [tid_ for tid_, st in changed if st == "AVAILABLE"]
        return {"ok": True, "task_id": tid, "unblocked": unblocked}
    finally:
        conn.close()


# --- discovery ---------------------------------------------------------------

def next_tasks(agent=None, limit=5, project=None) -> list:
    """Rank AVAILABLE work for an agent.

    Order: priority, then dependency-readiness (already guaranteed by status),
    then specialty match, then conflict-freedom, then age. Specialty is a
    ranking signal only -- never a restriction, because a team where only one
    agent may touch an area stalls the moment that agent is busy.
    """
    require_project(project)
    conn = connect()
    try:
        a = _resolve_agent(conn, agent) if agent else None
        specialties = json.loads(a["specialties"]) if a and a["specialties"] else []
        name = a["name"] if a else None
    finally:
        conn.close()
    out = []
    for t in list_tasks(status="AVAILABLE"):
        conf = check_conflicts(t["affected_files"], agent=name, task_id=t["task_id"]) \
            if t["affected_files"] else {"level": "NONE", "conflicts": []}
        hay = " ".join([t["title"], t["description"] or "", t["area"] or ""]).lower()
        spec_hits = sum(1 for s in specialties if s.lower() in hay)
        score = (
            PRIORITY_RANK.get(t["priority"], 9),
            0 if conf["level"] == "NONE" else (1 if conf["level"] == "WARNING" else 2),
            -spec_hits,
            t["created_at"] or 0,
        )
        t["_conflict"] = conf["level"]
        t["_specialty_match"] = spec_hits
        out.append((score, t))
    out.sort(key=lambda x: x[0])
    return [t for _, t in out[:limit]]


# --- messaging ---------------------------------------------------------------

def send_message(sender, recipient, content, msg_type="INFO", related_task=None,
                 related_files=None, project=None) -> dict:
    require_project(project)
    if msg_type not in MESSAGE_TYPES:
        raise AgopsError("bad message type %r (use %s)"
                         % (msg_type, ", ".join(MESSAGE_TYPES)))
    if not content or not content.strip():
        raise AgopsError("empty message")
    conn = connect()
    try:
        target = recipient.strip()
        if target.upper() == "ALL":
            target = "ALL"
            cfg = load_config()
            gap = cfg.get("broadcast_min_interval_s", 300)
            last = conn.execute(
                "SELECT created_at FROM messages WHERE sender=? AND recipient='ALL' "
                "ORDER BY id DESC LIMIT 1", (sender,)).fetchone()
            if last and _now() - last["created_at"] < gap:
                raise AgopsError(
                    "broadcast refused: %s broadcast %ds ago and the floor is %ds. "
                    "Broadcasts are for architecture changes, breaking APIs and "
                    "safety issues -- message the specific agent instead."
                    % (sender, int(_now() - last["created_at"]), gap))
        else:
            if _resolve_agent(conn, target) is None:
                raise AgopsError("no agent named %r on this project" % target)
            target = _resolve_agent(conn, target)["name"]
        conn.execute(
            "INSERT INTO messages(project_id, sender, recipient, msg_type, content, "
            "related_task, related_files, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (project_id(), sender, target, msg_type, content, related_task,
             json.dumps(related_files or []), _now()))
        _event(conn, sender, "message.send", target, msg_type)
        return {"ok": True, "to": target, "type": msg_type}
    finally:
        conn.close()


def inbox(agent, unread_only=True, mark_read=True, limit=50, project=None) -> list:
    require_project(project)
    conn = connect()
    try:
        a = _resolve_agent(conn, agent)
        name = a["name"] if a else agent
        q = ("SELECT * FROM messages WHERE project_id=? AND (recipient=? OR "
             "(recipient='ALL' AND sender<>?))")
        args = [project_id(), name, name]
        if unread_only:
            # Unread means unread BY ME. A broadcast stays unread for every
            # other recipient after one of them reads it.
            q += (" AND NOT EXISTS (SELECT 1 FROM message_reads r "
                  "WHERE r.message_id = messages.id AND r.agent = ?)")
            args.append(name)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        rows = [_row(r) for r in conn.execute(q, args)]
        if mark_read and rows:
            conn.executemany(
                "INSERT OR IGNORE INTO message_reads(message_id, agent, read_at) "
                "VALUES(?,?,?)", [(r["id"], name, _now()) for r in rows])
        for r in rows:
            try:
                r["related_files"] = json.loads(r.get("related_files") or "[]")
            except ValueError:
                r["related_files"] = []
        return rows
    finally:
        conn.close()


def request_handoff(sender, recipient, task_id, state, changed, remaining,
                    problems="", files=None, tests="", next_action="",
                    project=None) -> dict:
    """Hand a task to another agent with everything they need to continue."""
    require_project(project)
    body = "\n".join([
        "HANDOFF of %s" % task_id,
        "current state:  %s" % state,
        "what changed:   %s" % changed,
        "what remains:   %s" % remaining,
        "known problems: %s" % (problems or "none reported"),
        "files:          %s" % (", ".join(files or []) or "not specified"),
        "tests run:      %s" % (tests or "not reported"),
        "recommended:    %s" % (next_action or "continue as above"),
    ])
    send_message(sender, recipient, body, "HANDOFF", task_id, files, project=project)
    conn = connect()
    try:
        t = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if t and t["owner"] == sender:
            r = _resolve_agent(conn, recipient)
            conn.execute("UPDATE tasks SET owner=?, claimed_at=?, updated_at=? "
                         "WHERE task_id=?",
                         (r["name"] if r else recipient, _now(), _now(), task_id))
            conn.execute("UPDATE agents SET current_task=NULL, status='IDLE' "
                         "WHERE name=?", (sender,))
            _event(conn, sender, "task.handoff", task_id, "to " + recipient)
        return {"ok": True, "task_id": task_id, "to": recipient}
    finally:
        conn.close()


# --- staleness + recovery ----------------------------------------------------

def stale_agents(project=None) -> list:
    return [a for a in list_agents(project=project) if a["stale"]]


def recover(agent=None, dry_run=True, actor="human", project=None) -> dict:
    """Flag a crashed agent's work for review. Never discards anything.

    Deliberately NOT automatic. A stale agent's task is marked needs_recovery
    and moved to REVIEW; its files stay exactly as they are and its git state is
    reported, not touched. Somebody -- a human or an agent that has actually
    looked -- reclaims it explicitly afterwards.
    """
    require_project(project)
    cands = [a for a in list_agents(project=project)
             if a["stale"] or a["status"] == "OFFLINE"]
    if agent:
        cands = [a for a in cands if a["name"] == agent]
    report = []
    conn = connect()
    try:
        for a in cands:
            held = [_row(r) for r in conn.execute(
                "SELECT * FROM tasks WHERE owner=? AND status='IN_PROGRESS'",
                (a["name"],))]
            if not held:
                continue
            entry = {"agent": a["name"], "quiet_s": a["quiet_s"],
                     "tasks": [t["task_id"] for t in held],
                     "git": _git_dirty_report()}
            if not dry_run:
                for t in held:
                    conn.execute(
                        "UPDATE tasks SET status='REVIEW', needs_recovery=1, "
                        "recovery_note=?, updated_at=? WHERE task_id=?",
                        ("owner %s went quiet for %ds; work preserved, verify "
                         "before reclaiming" % (a["name"], a["quiet_s"]),
                         _now(), t["task_id"]))
                    _event(conn, actor, "task.recovery_flagged", t["task_id"],
                           "from " + a["name"])
                conn.execute("UPDATE agents SET status='OFFLINE' WHERE name=?",
                             (a["name"],))
            report.append(entry)
        return {"dry_run": dry_run, "recoverable": report}
    finally:
        conn.close()


def reclaim(tid, agent, verified=False, project=None) -> dict:
    """Take over a task flagged for recovery -- only after saying you checked."""
    require_project(project)
    if not verified:
        raise AgopsError(
            "%s is in recovery. Inspect the previous agent's work and git state "
            "first, then reclaim with verified=true." % tid)
    conn = connect()
    try:
        a = _resolve_agent(conn, agent)
        name = a["name"] if a else agent
        conn.execute("UPDATE tasks SET owner=?, status='IN_PROGRESS', "
                     "needs_recovery=0, claimed_at=?, updated_at=? WHERE task_id=?",
                     (name, _now(), _now(), tid))
        _event(conn, name, "task.reclaim", tid, "after recovery review")
        return get_task(tid)
    finally:
        conn.close()


def commit_states(shas) -> dict:
    """For each commit hash: 'pushed', 'local', 'missing' or 'unknown'.

    "Complete" is a claim about the board; "pushed" is a claim about the world.
    They come apart constantly -- an agent commits, its session ends, and the
    work sits on this machine only. A monitor that shows COMPLETE without
    saying whether anyone else can see it is telling half the truth, so this
    asks git rather than trusting the task record.
    """
    out = {}
    shas = [s for s in (shas or []) if s]
    if not shas:
        return out
    try:
        remote = subprocess.run(
            ["git", "-C", REPO, "rev-parse", "--verify", "--quiet", "origin/main"],
            capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        remote = ""
    for sha in shas:
        try:
            known = subprocess.run(["git", "-C", REPO, "cat-file", "-e", sha + "^{commit}"],
                                   capture_output=True, text=True, timeout=10)
            if known.returncode != 0:
                out[sha] = "missing"
                continue
            if not remote:
                out[sha] = "unknown"
                continue
            anc = subprocess.run(
                ["git", "-C", REPO, "merge-base", "--is-ancestor", sha, remote],
                capture_output=True, text=True, timeout=10)
            out[sha] = "pushed" if anc.returncode == 0 else "local"
        except Exception:
            out[sha] = "unknown"
    return out


def _git_dirty_report() -> dict:
    try:
        out = subprocess.run(["git", "-C", REPO, "status", "--porcelain"],
                             capture_output=True, text=True, timeout=15)
        files = [l[3:] for l in out.stdout.splitlines() if l.strip()]
        head = subprocess.run(["git", "-C", REPO, "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=10)
        return {"dirty_files": files[:40], "dirty_count": len(files),
                "head": head.stdout.strip()}
    except Exception:
        return {"dirty_files": [], "dirty_count": -1, "head": ""}


# --- resources ---------------------------------------------------------------

# --- the lead and the dispatch loop ------------------------------------------
# A "lead" is a coordination-only session: it dispatches, verifies, recovers
# and escalates, and never takes a task or edits repo code (refused in
# assign/claim above, blocked in the PreToolUse guard). These three functions
# are the wake mechanics that make a lead workable at all, because nothing can
# wake a stopped session: the lead stays alive by blocking in watch_events, and
# a worker that just completed stays reachable by blocking in await_dispatch.

def _lead_live(conn, cfg) -> bool:
    """Is an agent with role 'lead' on duty (non-OFFLINE, fresh heartbeat)?"""
    stale_after = cfg.get("stale_after_s", DEFAULT_STALE_S)
    return conn.execute(
        "SELECT 1 FROM agents WHERE role='lead' AND status<>'OFFLINE' "
        "AND last_heartbeat>=? LIMIT 1",
        (_now() - stale_after,)).fetchone() is not None


def _trailing_standby(conn, name) -> int:
    """How many consecutive agent.standby events this agent's history ends in.

    Any real activity by the agent (a claim, a completion, a message) inserts
    an event under its name and resets the run to zero, so the count is
    naturally "empty waits since I last did anything".
    """
    n = 0
    for r in conn.execute("SELECT kind FROM events WHERE actor=? "
                          "ORDER BY id DESC LIMIT 50", (name,)):
        if r["kind"] != "agent.standby":
            break
        n += 1
    return n


def maybe_enter_standby(agent, project=None) -> dict:
    """Should this agent wait for a dispatch instead of going cold?

    Yes only when ALL of these hold, each one a deliberate scope limit:
      * a lead is on duty -- with nobody dispatching, waiting is pure cost;
      * there is work that could plausibly arrive (an AVAILABLE task, or a
        BLOCKED one that a completion elsewhere may free);
      * the agent's own last real act was COMPLETING a task, recently -- this
        is the chaining moment. A session that was merely talking to the human
        must never be hijacked into a silent nine-minute wait;
      * fewer than standby_cycles empty waits since that completion. At the
        limit the answer is no, once, with an agent.standby_expired event so
        the lead learns this worker now needs a human wake. (The expired event
        itself breaks the recent-completion condition, so it cannot repeat.)
    """
    require_project(project)
    cfg = load_config()
    cycles = int(cfg.get("standby_cycles", 3))
    if cycles <= 0:
        return {"standby": False, "reason": "disabled"}
    conn = connect()
    try:
        a = _resolve_agent(conn, agent)
        if a is None or (a["role"] or "") == "lead":
            return {"standby": False, "reason": "not a worker"}
        name = a["name"]
        if a["current_task"]:
            return {"standby": False, "reason": "already holds a task"}
        if not _lead_live(conn, cfg):
            return {"standby": False, "reason": "no lead on duty"}
        if not conn.execute("SELECT 1 FROM tasks WHERE status IN "
                            "('AVAILABLE','BLOCKED') LIMIT 1").fetchone():
            return {"standby": False, "reason": "no work that could arrive"}
        last_real = conn.execute(
            "SELECT kind, ts FROM events WHERE actor=? AND kind<>'agent.standby' "
            "ORDER BY id DESC LIMIT 1", (name,)).fetchone()
        recent = cfg.get("standby_recent_complete_s", 900)
        if (last_real is None or last_real["kind"] != "task.complete"
                or _now() - last_real["ts"] > recent):
            return {"standby": False, "reason": "not fresh off a completion"}
        waited = _trailing_standby(conn, name)
        if waited >= cycles:
            _event(conn, name, "agent.standby_expired", name,
                   "%d empty waits; going cold -- a new dispatch to this agent "
                   "now needs a human to wake its terminal" % waited)
            return {"standby": False, "reason": "standby budget spent",
                    "expired": True}
        _event(conn, name, "agent.standby", name, "wait %d/%d" % (waited + 1, cycles))
        return {"standby": True, "cycle": waited + 1, "cycles": cycles,
                "wait_s": cfg.get("standby_wait_s", 540)}
    finally:
        conn.close()


def await_dispatch(agent, timeout_s=540, poll_s=2.0, project=None) -> dict:
    """Block until a task is dispatched to this agent, or the timeout passes.

    The command does the waiting so the model does not have to: an agent in
    standby runs this and burns nothing while blocked. Returns the dispatched
    task, or {"dispatched": False} on timeout -- never an error, because "no
    work came" is a normal outcome. Heartbeats while waiting, so a waiting
    agent reads WAITING on the board rather than decaying toward stale.
    """
    require_project(project)
    conn = connect()
    try:
        a = _resolve_agent(conn, agent)
        if a is None:
            raise AgopsError("unknown agent %r -- register first" % agent)
        name, aid = a["name"], a["agent_id"]
        conn.execute("UPDATE agents SET status='WAITING', last_heartbeat=? "
                     "WHERE agent_id=?", (_now(), aid))
        deadline = _now() + max(1, timeout_s)
        while _now() < deadline:
            r = conn.execute("SELECT current_task FROM agents WHERE agent_id=?",
                             (aid,)).fetchone()
            if r and r["current_task"]:
                return {"dispatched": True, "task": get_task(r["current_task"])}
            conn.execute("UPDATE agents SET last_heartbeat=? WHERE agent_id=?",
                         (_now(), aid))
            time.sleep(poll_s)
        conn.execute("UPDATE agents SET status='IDLE', last_heartbeat=? "
                     "WHERE agent_id=? AND status='WAITING'", (_now(), aid))
        return {"dispatched": False}
    finally:
        conn.close()


def watch_events(since=None, timeout_s=540, poll_s=2.0, agent=None,
                 project=None) -> dict:
    """Block until anything new lands in the event log, or the timeout passes.

    The lead's keep-alive: wake, act, watch again. Pass the returned
    `next_since` into the next call so nothing that happened while you were
    acting is missed; with no `since` it starts from now. Times out clean
    (empty events, same cursor) so a quiet board costs one short turn per
    timeout rather than a stream of polling output.
    """
    require_project(project)
    conn = connect()
    try:
        if since is None:
            r = conn.execute("SELECT COALESCE(MAX(id), 0) m FROM events").fetchone()
            since = r["m"]
        aid = None
        if agent:
            a = _resolve_agent(conn, agent)
            aid = a["agent_id"] if a else None
        deadline = _now() + max(1, timeout_s)
        while True:
            rows = [_row(r) for r in conn.execute(
                "SELECT * FROM events WHERE id>? ORDER BY id", (since,))]
            if rows:
                return {"events": rows, "next_since": rows[-1]["id"],
                        "timed_out": False}
            if _now() >= deadline:
                return {"events": [], "next_since": since, "timed_out": True}
            if aid:
                conn.execute("UPDATE agents SET last_heartbeat=? WHERE agent_id=?",
                             (_now(), aid))
            time.sleep(poll_s)
    finally:
        conn.close()


def take_resource(name, agent, reason="", queue=False, project=None) -> dict:
    """Take an exclusive resource, or optionally get in line for it.

    `queue` exists because the manual alternative cost real time: bravo finished
    TASK-006's code, needed sitl-5760 to verify, and sat idle behind a
    "ping me when you drop it" handshake that depended on alpha remembering.
    Nothing can wake a stopped session, so the handoff has to be a message the
    holder's `drop` sends automatically rather than one a human relays.
    """
    require_project(project)
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        r = conn.execute("SELECT * FROM resources WHERE name=?", (name,)).fetchone()
        if r and r["holder"] and r["holder"] != agent:
            holder = r["holder"]
            if queue:
                conn.execute(
                    "INSERT INTO resource_waits(resource, agent, since, note) "
                    "VALUES(?,?,?,?) ON CONFLICT(resource, agent) DO NOTHING",
                    (name, agent, _now(), reason))
            conn.execute("COMMIT")
            if queue:
                _event(conn, agent, "resource.queue", name, reason)
                ahead = [w["agent"] for w in conn.execute(
                    "SELECT agent FROM resource_waits WHERE resource=? "
                    "ORDER BY since", (name,))]
                return {"ok": False, "queued": True, "resource": name,
                        "holder": holder, "place": ahead.index(agent) + 1,
                        "message": "%s is held by %s -- you are #%d in line and "
                                   "will be messaged when it frees"
                                   % (name, holder, ahead.index(agent) + 1)}
            return {"ok": False, "resource": name, "holder": holder,
                    "message": "%s is held by %s" % (name, holder)}
        conn.execute("INSERT INTO resources(name, holder, taken_at, reason) "
                     "VALUES(?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
                     "holder=excluded.holder, taken_at=excluded.taken_at, "
                     "reason=excluded.reason", (name, agent, _now(), reason))
        conn.execute("DELETE FROM resource_waits WHERE resource=? AND agent=?",
                     (name, agent))
        conn.execute("COMMIT")
        _event(conn, agent, "resource.take", name, reason)
        return {"ok": True, "resource": name, "holder": agent}
    finally:
        conn.close()


def drop_resource(name, agent, project=None) -> dict:
    """Release a resource and tell the next agent in line, if there is one.

    Skips waiters that are OFFLINE rather than messaging a closed terminal and
    calling the handoff done -- a queue that hands the lock to a dead session is
    worse than no queue, because the next agent believes someone else has it.
    """
    require_project(project)
    conn = connect()
    try:
        conn.execute("UPDATE resources SET holder=NULL WHERE name=? AND holder=?",
                     (name, agent))
        _event(conn, agent, "resource.drop", name)

        live = {a["name"] for a in list_agents(include_offline=False, project=project)}
        nxt = None
        for w in conn.execute("SELECT agent FROM resource_waits WHERE resource=? "
                              "ORDER BY since", (name,)):
            if w["agent"] in live and w["agent"] != agent:
                nxt = w["agent"]
                break
        if nxt is None:
            return {"ok": True, "resource": name, "notified": None}

        conn.execute("DELETE FROM resource_waits WHERE resource=? AND agent=?",
                     (name, nxt))
        _event(conn, agent, "resource.handoff", name, nxt)
    finally:
        conn.close()

    send_message(agent, nxt,
                 "%s is free -- you were next in line. Take it now: "
                 "py tools\\agops.py take %s" % (name, name),
                 msg_type="INFO", project=project)
    return {"ok": True, "resource": name, "notified": nxt}


def resource_waiters(name=None, project=None) -> list:
    """Who is in line, oldest first."""
    require_project(project)
    conn = connect()
    try:
        if name:
            rows = conn.execute("SELECT * FROM resource_waits WHERE resource=? "
                                "ORDER BY since", (name,))
        else:
            rows = conn.execute("SELECT * FROM resource_waits ORDER BY resource, since")
        return [_row(r) for r in rows]
    finally:
        conn.close()


# --- project status ----------------------------------------------------------

def project_status(project=None) -> dict:
    require_project(project)
    cfg = load_config()
    agents = list_agents(project=project)
    conn = connect()
    try:
        events = [_row(r) for r in conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT 12")]
        resources = [_row(r) for r in conn.execute(
            "SELECT * FROM resources WHERE holder IS NOT NULL")]
    finally:
        conn.close()
    tasks = {s: list_tasks(status=s) for s in
             ("AVAILABLE", "IN_PROGRESS", "BLOCKED", "REVIEW")}
    conflicts = []
    for t in tasks["IN_PROGRESS"]:
        c = check_conflicts(t["affected_files"], agent=t["owner"], task_id=t["task_id"])
        conflicts.extend(c["conflicts"])
    return {
        "project_id": cfg.get("project_id"),
        "project_name": cfg.get("project_name"),
        "coordination_enabled": cfg.get("coordination_enabled", True),
        "enforcement": cfg.get("enforcement", "advisory"),
        "agents": agents,
        "tasks": tasks,
        "conflicts": conflicts,
        "resources": resources,
        "recent_events": events,
        "stale": [a["name"] for a in agents if a["stale"]],
    }
