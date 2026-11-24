#!/usr/bin/env python3
"""Claude Code PreToolUse hook for the Agent tool: route each subagent to a model and effort automatically.

Reads the hook's JSON on standard input, scores the subagent's prompt (and the repository it runs in) with
router.py, and answers with `updatedInput` carrying `model` and `effort`. It never blocks Claude: on any error,
or when the call is skipped, it prints nothing and exits 0, and Claude Code goes on as if there were no hook.

Skipped: hook disabled in the config, a subagent type in `hook.skip_agent_types`, or (with `respect_explicit`)
a call that already names its model or effort. Every decision is appended to `hook.runs_log` (JSON lines).
"""
from __future__ import annotations

import datetime
import json
import pathlib
import sys
from typing import Any, Dict, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import router  # noqa: E402


def decide(event: Dict[str, Any], cfg: Dict[str, Any], cwd: pathlib.Path,
           env: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
    """The hook's answer, or None to leave the call alone. Also returns the log entry under '_log'."""
    hk = cfg["hook"]
    if not hk.get("enabled", True) or event.get("tool_name") not in ("Agent", "Task"):
        return None
    ti = dict(event.get("tool_input") or {})
    kind = str(ti.get("subagent_type") or "")
    entry: Dict[str, Any] = {"time": datetime.datetime.now().isoformat(timespec="seconds"),
                             "session": event.get("session_id", ""), "agent_type": kind,
                             "description": str(ti.get("description") or "")[:120]}
    if kind in hk.get("skip_agent_types", []):
        return {"_log": {**entry, "action": "skipped (agent type)"}}
    if hk.get("respect_explicit", True) and (ti.get("model") or ti.get("effort")):
        return {"_log": {**entry, "action": "kept (explicit)", "model": ti.get("model"), "effort": ti.get("effort")}}
    text = "\n\n".join(str(ti.get(k) or "") for k in ("description", "prompt"))
    repo = cwd if (cwd / ".git").exists() else None
    r = router.score_text(text, cfg, repo=repo)
    overrides = router.check_env(env)
    out: Dict[str, Any] = {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "updatedInput": {**ti, "model": r["model"], "effort": r["effort"]},
    }}
    if hk.get("permission_decision") in ("allow", "ask"):
        out["hookSpecificOutput"]["permissionDecision"] = hk["permission_decision"]
        out["hookSpecificOutput"]["permissionDecisionReason"] = f"model-effort-router: {r['model']}/{r['effort']}"
    out["_log"] = {**entry, "action": "routed", "model": r["model"], "effort": r["effort"], "score": r["score"],
                   "reasons": r["reasons"], "overridden_by": overrides}
    return out


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
        cwd = pathlib.Path(event.get("cwd") or ".")
        cfg = router.load_config(cwd / router.PROJECT_CONFIG)
        ans = decide(event, cfg, cwd)
        if ans is None:
            return 0
        log = ans.pop("_log", None)
        if log is not None:
            try:
                lp = cwd / cfg["hook"]["runs_log"]
                lp.parent.mkdir(parents=True, exist_ok=True)
                with lp.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(log) + "\n")
            except OSError:
                pass
        if ans:
            print(json.dumps(ans))
    except Exception:  # noqa: BLE001  a hook must never break the session
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
