#!/usr/bin/env python
"""AgOps MCP server: coordination as native tools, over stdio.

WHY MCP AND NOT JUST THE CLI. Both exist on purpose. MCP gives the model typed
tools with schemas it can call directly -- no shell quoting, no output parsing,
and the tool list itself documents the protocol so a fresh session discovers how
to coordinate without being told. The CLI is the fallback and the human surface:
if this server fails to start, every operation is still one shell command away.
That is the graceful-degradation requirement made concrete rather than promised.

This is a hand-rolled JSON-RPC 2.0 stdio server (initialize / tools/list /
tools/call) because the alternative is a pip dependency in a repo that currently
needs none. Roughly 200 lines against an external package and a version to keep
current is the right trade here.

Registered project-scoped in .mcp.json, so it only ever attaches to sessions
opened in this repository -- which is also the project-isolation guarantee: a
session in another checkout never loads this server at all.
"""
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agops import core  # noqa: E402
from agops.core import AgopsError  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"

S_STR = {"type": "string"}
S_BOOL = {"type": "boolean"}
S_LIST = {"type": "array", "items": {"type": "string"}}


def _agent_arg(desc="Acting agent name (e.g. alpha). Defaults to AGOPS_AGENT."):
    return {"type": "string", "description": desc}


TOOLS = [
    {
        "name": "agops_status",
        "description": "Whole-team snapshot: agents and their status, tasks by "
                       "state, active conflicts, held resources, recent events. "
                       "Call this first in any session that is picking up work.",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": lambda a: core.project_status(),
    },
    {
        "name": "agops_register",
        "description": "Register or re-attach this session as a team agent. "
                       "Idempotent per session_id: re-running never creates a "
                       "duplicate identity.",
        "inputSchema": {"type": "object", "properties": {
            "session_id": S_STR, "name": S_STR, "role": S_STR,
            "specialties": S_LIST}},
        "handler": lambda a: core.register_agent(
            session_id=a.get("session_id") or os.environ.get("CLAUDE_SESSION_ID"),
            name=a.get("name"), role=a.get("role"),
            specialties=a.get("specialties"), cwd=os.getcwd()),
    },
    {
        "name": "agops_list_agents",
        "description": "Every registered agent with status, current task, "
                       "specialties and staleness.",
        "inputSchema": {"type": "object", "properties": {"include_offline": S_BOOL}},
        "handler": lambda a: core.list_agents(a.get("include_offline", True)),
    },
    {
        "name": "agops_heartbeat",
        "description": "Refresh liveness and optionally set status "
                       "(WORKING/IDLE/BLOCKED/REVIEWING/WAITING/ERROR).",
        "inputSchema": {"type": "object",
                        "properties": {"agent": _agent_arg(), "status": S_STR,
                                       "note": S_STR},
                        "required": ["agent"]},
        "handler": lambda a: core.heartbeat(a["agent"], a.get("status"), a.get("note")),
    },
    {
        "name": "agops_list_tasks",
        "description": "Tasks, optionally filtered by status or owner.",
        "inputSchema": {"type": "object", "properties": {"status": S_STR, "owner": S_STR}},
        "handler": lambda a: core.list_tasks(a.get("status"), a.get("owner")),
    },
    {
        "name": "agops_get_task",
        "description": "One task in full: dependencies, dependents, affected "
                       "files, completion record.",
        "inputSchema": {"type": "object", "properties": {"task_id": S_STR},
                        "required": ["task_id"]},
        "handler": lambda a: core.get_task(a["task_id"]),
    },
    {
        "name": "agops_create_task",
        "description": "Create a task. Ground it in a real requirement, failing "
                       "test, TODO or discovered blocker -- speculative tasks "
                       "pollute the queue for every agent.",
        "inputSchema": {"type": "object", "properties": {
            "title": S_STR, "description": S_STR,
            "priority": {"type": "string", "enum": list(core.PRIORITIES)},
            "depends_on": S_LIST, "files": S_LIST, "area": S_STR,
            "estimate": S_STR, "created_by": S_STR},
            "required": ["title"]},
        "handler": lambda a: core.create_task(
            a["title"], a.get("description", ""), a.get("priority", "MEDIUM"),
            a.get("depends_on"), a.get("files"), a.get("area"),
            a.get("created_by", "agent"), a.get("estimate")),
    },
    {
        "name": "agops_next_tasks",
        "description": "Ranked available work for an agent: priority, then "
                       "conflict-freedom, then specialty match, then age. Use "
                       "this instead of searching the repo for something to do.",
        "inputSchema": {"type": "object",
                        "properties": {"agent": _agent_arg(),
                                       "limit": {"type": "integer"}}},
        "handler": lambda a: core.next_tasks(a.get("agent"), a.get("limit", 5)),
    },
    {
        "name": "agops_claim_task",
        "description": "Atomically take a task. Exactly one agent can win a "
                       "race; the loser is told who owns it. Refuses when the "
                       "task's files overlap another live agent's work unless "
                       "force is set after agreeing with them.",
        "inputSchema": {"type": "object",
                        "properties": {"task_id": S_STR, "agent": _agent_arg(),
                                       "force": S_BOOL},
                        "required": ["task_id", "agent"]},
        "handler": lambda a: core.claim_task(a["task_id"], a["agent"],
                                             a.get("force", False)),
    },
    {
        "name": "agops_release_task",
        "description": "Give a task back to the pool.",
        "inputSchema": {"type": "object",
                        "properties": {"task_id": S_STR, "agent": _agent_arg(),
                                       "reason": S_STR},
                        "required": ["task_id", "agent"]},
        "handler": lambda a: core.release_task(a["task_id"], a["agent"],
                                               a.get("reason", "")),
    },
    {
        "name": "agops_complete_task",
        "description": "Mark a task COMPLETE and auto-unblock its dependents. "
                       "Requires a real summary; refuses outright if you report "
                       "failing tests. Do not report success you have not verified.",
        "inputSchema": {"type": "object", "properties": {
            "task_id": S_STR, "agent": _agent_arg(), "summary": S_STR,
            "verification": S_STR, "commit_hash": S_STR, "tests_passed": S_BOOL},
            "required": ["task_id", "agent", "summary"]},
        "handler": lambda a: core.complete_task(
            a["task_id"], a["agent"], a["summary"], a.get("verification", ""),
            a.get("commit_hash", ""), a.get("tests_passed")),
    },
    {
        "name": "agops_block_task",
        "description": "Mark a task BLOCKED with a reason.",
        "inputSchema": {"type": "object",
                        "properties": {"task_id": S_STR, "agent": _agent_arg(),
                                       "reason": S_STR},
                        "required": ["task_id", "reason"]},
        "handler": lambda a: core.block_task(a["task_id"], a.get("agent", "agent"),
                                             a["reason"]),
    },
    {
        "name": "agops_unblock_task",
        "description": "Clear a manual block; dependency state is recomputed.",
        "inputSchema": {"type": "object",
                        "properties": {"task_id": S_STR, "agent": _agent_arg()},
                        "required": ["task_id"]},
        "handler": lambda a: core.unblock_task(a["task_id"], a.get("agent", "agent")),
    },
    {
        "name": "agops_check_conflicts",
        "description": "Before editing files you have not claimed: who else is "
                       "in them. BLOCKING means a live agent owns an "
                       "overlapping path; WARNING is advisory area overlap.",
        "inputSchema": {"type": "object",
                        "properties": {"files": S_LIST, "agent": _agent_arg()},
                        "required": ["files"]},
        "handler": lambda a: core.check_conflicts(a["files"], a.get("agent")),
    },
    {
        "name": "agops_get_file_owners",
        "description": "Which agents are currently working one path, and its area.",
        "inputSchema": {"type": "object", "properties": {"path": S_STR},
                        "required": ["path"]},
        "handler": lambda a: core.get_file_owners(a["path"]),
    },
    {
        "name": "agops_send_message",
        "description": "Message one teammate directly. Types: INFO, QUESTION, "
                       "WARNING, BLOCKER, HANDOFF, REVIEW_REQUEST, COMPLETION. "
                       "Use this instead of committing a file to say something.",
        "inputSchema": {"type": "object", "properties": {
            "sender": _agent_arg("Your agent name"), "recipient": S_STR,
            "content": S_STR,
            "message_type": {"type": "string", "enum": list(core.MESSAGE_TYPES)},
            "related_task": S_STR, "related_files": S_LIST},
            "required": ["sender", "recipient", "content"]},
        "handler": lambda a: core.send_message(
            a["sender"], a["recipient"], a["content"],
            a.get("message_type", "INFO"), a.get("related_task"),
            a.get("related_files")),
    },
    {
        "name": "agops_broadcast",
        "description": "Message the whole project. Rate-limited on purpose -- "
                       "for architecture changes, breaking APIs, safety issues "
                       "and migrations only, never status updates.",
        "inputSchema": {"type": "object", "properties": {
            "sender": _agent_arg("Your agent name"), "content": S_STR,
            "message_type": {"type": "string", "enum": list(core.MESSAGE_TYPES)},
            "related_task": S_STR},
            "required": ["sender", "content"]},
        "handler": lambda a: core.send_message(
            a["sender"], "ALL", a["content"], a.get("message_type", "INFO"),
            a.get("related_task")),
    },
    {
        "name": "agops_inbox",
        "description": "Read messages addressed to you (and broadcasts).",
        "inputSchema": {"type": "object",
                        "properties": {"agent": _agent_arg(), "unread_only": S_BOOL,
                                       "mark_read": S_BOOL},
                        "required": ["agent"]},
        "handler": lambda a: core.inbox(a["agent"], a.get("unread_only", True),
                                        a.get("mark_read", True)),
    },
    {
        "name": "agops_handoff",
        "description": "Transfer a task with everything the receiver needs: "
                       "state, what changed, what remains, problems, files, "
                       "tests run, recommended next action.",
        "inputSchema": {"type": "object", "properties": {
            "sender": _agent_arg("Your agent name"), "recipient": S_STR,
            "task_id": S_STR, "state": S_STR, "changed": S_STR,
            "remaining": S_STR, "problems": S_STR, "files": S_LIST,
            "tests": S_STR, "next_action": S_STR},
            "required": ["sender", "recipient", "task_id", "state", "changed",
                         "remaining"]},
        "handler": lambda a: core.request_handoff(
            a["sender"], a["recipient"], a["task_id"], a["state"], a["changed"],
            a["remaining"], a.get("problems", ""), a.get("files"),
            a.get("tests", ""), a.get("next_action", "")),
    },
    {
        "name": "agops_recover",
        "description": "Find agents that went quiet while holding work. Dry-run "
                       "by default. Never discards anything: it flags tasks for "
                       "review and reports git state so a human or agent can look.",
        "inputSchema": {"type": "object",
                        "properties": {"agent": S_STR, "apply": S_BOOL}},
        "handler": lambda a: core.recover(a.get("agent"), not a.get("apply", False)),
    },
    {
        "name": "agops_reclaim",
        "description": "Take over a task flagged for recovery. Requires "
                       "verified=true, meaning you actually inspected the "
                       "previous agent's work and the git state.",
        "inputSchema": {"type": "object",
                        "properties": {"task_id": S_STR, "agent": _agent_arg(),
                                       "verified": S_BOOL},
                        "required": ["task_id", "agent"]},
        "handler": lambda a: core.reclaim(a["task_id"], a["agent"],
                                          a.get("verified", False)),
    },
    {
        "name": "agops_take_resource",
        "description": "Take an exclusive resource (sitl-5760, serial-fc, "
                       "exe-build, git-push). Drop it as soon as you are done.",
        "inputSchema": {"type": "object",
                        "properties": {"resource": S_STR, "agent": _agent_arg(),
                                       "reason": S_STR},
                        "required": ["resource", "agent"]},
        "handler": lambda a: core.take_resource(a["resource"], a["agent"],
                                                a.get("reason", "")),
    },
    {
        "name": "agops_drop_resource",
        "description": "Release an exclusive resource.",
        "inputSchema": {"type": "object",
                        "properties": {"resource": S_STR, "agent": _agent_arg()},
                        "required": ["resource", "agent"]},
        "handler": lambda a: core.drop_resource(a["resource"], a["agent"]),
    },
]

BY_NAME = {t["name"]: t for t in TOOLS}


def _public(tool):
    return {k: tool[k] for k in ("name", "description", "inputSchema")}


def handle(req):
    method = req.get("method")
    rid = req.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "agops", "version": "1.0.0"},
            "instructions":
                "AgOps team coordination for this repository. Register once, "
                "then: agops_status to see the board, agops_next_tasks to find "
                "work, agops_claim_task before implementing anything, "
                "agops_check_conflicts before editing files you did not claim, "
                "agops_send_message to tell a teammate something, and "
                "agops_complete_task with a verified summary when done."}}
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid,
                "result": {"tools": [_public(t) for t in TOOLS]}}
    if method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = BY_NAME.get(name)
        if tool is None:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601, "message": "no such tool %r" % name}}
        try:
            result = tool["handler"](args)
            text = json.dumps(result, indent=2, default=str)
            return {"jsonrpc": "2.0", "id": rid,
                    "result": {"content": [{"type": "text", "text": text}]}}
        except AgopsError as exc:
            # A refusal is a RESULT, not a transport error: the model should read
            # "TASK-042 is already owned by alpha" and act on it, not see a crash.
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": "REFUSED: %s" % exc}],
                "isError": True}}
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text":
                             "AgOps coordination error: %s\nCoordination may be "
                             "degraded -- work can continue, but say so rather "
                             "than assuming claims are enforced.\n%s"
                             % (exc, traceback.format_exc(limit=3))}],
                "isError": True}}
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": "unknown method %r" % method}}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        resp = handle(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
