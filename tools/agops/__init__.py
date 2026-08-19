"""AgOps multi-agent coordination for this repository.

See .agops/README.md for the full picture. The short version:

    core.py                 state: agents, tasks, messages, ownership, events
    cli.py                  human/agent entry point (py tools\\agops.py ...)
    mcp_server.py           the same operations as native MCP tools
    hook_*.py               deterministic automation wired in .claude/settings.json

Nothing in here may raise into a caller's critical path. Coordination is an aid;
when it breaks, work continues.
"""

__all__ = ["core"]
