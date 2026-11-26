#!/usr/bin/env python3
"""model-effort-router: score a task and choose the Claude model and effort a worker should run it on.

    python3 router.py init                                   # write .claude/model-router.json to edit
    python3 router.py score TASK.md [TASK2.md ...]           # a task file (story, packet, issue saved as markdown)
    python3 router.py score --issue owner/repo#123           # a GitHub issue (public, or with GITHUB_TOKEN)
    python3 router.py score --text "Fix the login timeout"   # a plain prompt; `-` reads standard input
    python3 router.py score TASK.md --repo .                 # add facts from the git repository
    python3 router.py score TASK.md --write                  # put the choice line into the task file
    python3 router.py score TASK.md --write --retry 1 --cause worker
    python3 router.py score TASK.md --retry 1 --failure-log out.txt   # the cause is suggested from the output
    python3 router.py cause out.txt                          # whose failure was it: worker, environment or task?
    python3 router.py check-env                              # settings that would override the choice
    python3 router.py audit                                  # what the hook did: routed, kept, skipped
    python3 router.py budget                                 # spend against your daily, weekly, monthly limits
    python3 router.py record TASK.md --ran sonnet/high --first-try pass
    python3 router.py summary                                # the outcome log, with suggested point changes

How it decides (every number lives in the config, not in this file):
  1. Critical: a `**Critical:** yes` line, a `critical` label, or a touched file under a configured critical path puts
     the task on the top rung. Words alone are only named ("the text may touch the critical part"), never acted on.
  2. Risk: a `**Risk:** money|security|data` line, a mapped label, or a touched file under a configured risk path
     lifts the task to at least the risk floor. Risk words alone are named, never acted on.
  3. Points: size, acceptance criteria, dependencies, signals in the text, and (with --repo) how many files the
     task touches, whether they have tests and how often they changed lately.
  4. Retries: only the worker's own failures (`--cause worker`) move it up a rung.
  5. `record` each finished task; `summary` reads the log back and suggests point changes.

Standard library only. Python 3.9 or later. The only network call is --issue, to api.github.com.
"""
from __future__ import annotations

import argparse
import datetime
import fnmatch
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.request
from typing import Any, Dict, List, Optional, Set, Tuple

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE.parent / "config" / "default.json"
PROJECT_CONFIG = pathlib.Path(".claude") / "model-router.json"
PROJECT_SETTINGS = pathlib.Path(".claude") / "settings.json"   # Claude Code's own project settings
PLUGIN_ID = "model-effort-router@model-effort-router"
# `share` puts a copy of the router into a project, so that everyone who opens the project is routed with nothing
# to install: these files, and one hook in the project's .claude/settings.json that runs the copy.
SHARED_DIR = pathlib.Path(".claude") / "model-effort-router"
SHARED_FILES = ["scripts/router.py", "scripts/route_agent.py", "config/default.json", "LICENSE"]
SHARED_MARK = ".claude/model-effort-router/scripts/route_agent.py"
MARKETPLACE = "model-effort-router"
NEEDS_PYTHON = "import sys; sys.exit(sys.version_info < (3, 9))"
NO_PYTHON = ("model-effort-router: Python 3.9 or later was not found on this machine, so subagents are not being "
             "routed. Install Python (python.org) and start a new session.")


def hook_commands(script: str) -> Dict[str, str]:
    """The two shell commands of the hook, for the script at `script` (a path with a ${...} placeholder).

    `route` runs the router under the first Python that works (python3, python, or the Windows launcher) and never
    fails the session. `check` runs once when a session starts and tells the user, in one line, when no Python
    3.9 or later is there: without that line the router would simply do nothing."""
    return {
        "route": f"for py in python3 python 'py -3'; do $py \"{script}\" 2>/dev/null && exit 0; done; exit 0",
        "check": f"for py in python3 python 'py -3'; do $py -c '{NEEDS_PYTHON}' 2>/dev/null && exit 0; done; "
                 "echo '" + json.dumps({"systemMessage": NO_PYTHON}) + "'",
    }


MARK = "**Model and effort (model-effort-router):**"
CAUSES = ("worker", "environment", "task")   # --cause; "unknown" and "repeat" are set by --failure-log
VERSION = "0.8.0"

# Settings that make Claude Code run on something other than what the router chose (Claude Code docs, Nov 2026).
OVERRIDES = {
    "CLAUDE_CODE_EFFORT_LEVEL": "overrides every other effort setting, including the Agent tool's effort and a subagent's `effort:`",
    "CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "makes subagents ignore the model they are given",
    "ANTHROPIC_MODEL": "sets a separate session's model when it is started without --model",
}

LOG_COLUMNS = ["Date", "Task", "Score", "Critical", "Risk", "Signals", "Chosen", "Ran", "First try",
               "Worker retries", "Other failures", "Review findings", "Rework", "Note"]
ISSUE_REF = re.compile(r"^(?:https?://github\.com/)?([\w.-]+)/([\w.-]+)(?:#|/issues/)(\d+)$")


# ---- config --------------------------------------------------------------------------------------------------

def load_config(path: Optional[pathlib.Path] = None) -> Dict[str, Any]:
    """The bundled default rules, with the project's .claude/model-router.json laid over them if there is one.

    A key the project sets replaces the default. Inside a section (hook, budget, risk, critical, ...) only the
    fields the project names change, so a short file such as {"mechanical": {"enabled": true}} is enough."""
    cfg: Dict[str, Any] = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    p = path or PROJECT_CONFIG
    if p.exists():
        own = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(own, dict):
            raise ValueError("it has to be a JSON object: { ... }")
        for k, v in own.items():
            cfg[k] = {**cfg[k], **v} if isinstance(v, dict) and isinstance(cfg.get(k), dict) else v
    return cfg


# ---- inputs --------------------------------------------------------------------------------------------------

class Task:
    """What the router scores: the text, labels set by people (GitHub issues), and a name for the log."""

    def __init__(self, text: str, name: str, labels: Optional[List[str]] = None, path: Optional[pathlib.Path] = None):
        self.text, self.name, self.labels, self.path = text, name, labels or [], path


def fetch_issue(ref: str, opener: Any = None) -> Task:
    m = ISSUE_REF.match(ref.strip())
    if not m:
        raise ValueError(f"not an issue reference: {ref!r} (use owner/repo#123 or the issue URL)")
    owner, repo, num = m.groups()
    req = urllib.request.Request(f"https://api.github.com/repos/{owner}/{repo}/issues/{num}",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "model-effort-router"})
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with (opener or urllib.request.urlopen)(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    labels = [lb["name"] if isinstance(lb, dict) else str(lb) for lb in data.get("labels", [])]
    text = f"# {data.get('title', '')}\n\n{data.get('body') or ''}\n"
    return Task(text, f"{owner}/{repo}#{num}", labels)


def load_task(target: str) -> Task:
    if target == "-":
        return Task(sys.stdin.read(), "stdin")
    if ISSUE_REF.match(target) and not pathlib.Path(target).exists():
        return fetch_issue(target)
    p = pathlib.Path(target)
    return Task(p.read_text(encoding="utf-8"), p.name, path=p)


# ---- reading a task ------------------------------------------------------------------------------------------

def said(pattern: str, text: str, negation: str) -> bool:
    """True when a sentence matches the pattern and does not deny it (no, not, never, unchanged ...)."""
    for s in re.split(r"(?<=[.;:])\s+|\n", text):
        if re.search(pattern, s, re.I) and not re.search(negation, s, re.I):
            return True
    return False


def field(text: str, name: str) -> Optional[str]:
    """A `**Name:** value` line, or the same marker inside a sentence (value up to the end of the clause)."""
    m = re.search(rf"\*\*{re.escape(name)}:\*\*\s*([^\n.;]+)", text, re.I)
    return m.group(1).strip() if m else None


def task_body(text: str, cfg: Dict[str, Any]) -> str:
    """The text the points are read from: without the router's own lines and without lines the config skips."""
    skip = cfg.get("skip_lines")
    out = []
    for ln in text.splitlines():
        if ln.startswith(MARK) or re.match(r"^\*\*(Critical|Risk):\*\*", ln, re.I):
            continue
        if skip and re.search(skip, ln, re.I):
            continue
        out.append(ln)
    return "\n".join(out)


def criteria_items(text: str, cfg: Dict[str, Any]) -> List[str]:
    """The acceptance criteria, one string each, from the criteria section of the task."""
    c = cfg["criteria"]
    parts = re.split(c["start"], text, maxsplit=1, flags=re.I)
    if len(parts) < 2:
        return []
    body = re.split(c["end"], parts[1], maxsplit=1, flags=re.I | re.M)[0]
    items = []
    for ln in body.splitlines():
        m = re.match(c["item"], ln)
        if m:
            items.append(ln[m.end():].strip())
    return items


def criteria_count(text: str, cfg: Dict[str, Any]) -> int:
    return len(criteria_items(text, cfg))


def stated_size(body: str, labels: List[str], cfg: Dict[str, Any]) -> str:
    """The size the task or its labels state, or "?"."""
    m = re.search(cfg["size"]["pattern"], body, re.I)
    if m:
        return m.group(1).upper()
    for lb in labels:
        lm = re.match(cfg["labels"]["size"], lb, re.I)
        if lm:
            return lm.group(1).upper()
    return "?"


def readiness(text: str, cfg: Dict[str, Any]) -> List[str]:
    """Why the task is not clear enough to start, if it is not: a vague task fails on every model, so this is
    worth knowing before any tokens are spent. Advice only: it never changes the model or effort."""
    rd = cfg["readiness"]
    if not rd.get("enabled", True):
        return []
    notes: List[str] = []
    items = criteria_items(text, cfg)
    if items:
        words: Set[str] = set()
        vague = 0
        for it in items:
            hit = [w for w in rd["vague"] if re.search(rf"\b{re.escape(w)}\b", it, re.I)]
            if hit:
                vague += 1
                words.update(w.lower() for w in hit)
        if vague:
            notes.append(f"{vague} of {len(items)} criteria are vague ({', '.join(sorted(words)[:4])}): "
                         "say what must be observed")
        short = sum(1 for it in items if len(it.split()) < rd["min_words"])
        if short:
            notes.append(f"{short} of {len(items)} criteria are under {rd['min_words']} words")
    elif len(text.split()) >= rd["criteria_expected_over_words"]:
        notes.append("no acceptance criteria: nothing to check the result against")
    if said(rd["open_question"], text, cfg["negation"]):
        notes.append("an open question: a person has to answer it first")
    return notes


# ---- the repository ------------------------------------------------------------------------------------------

def _git(repo: pathlib.Path, *args: str) -> List[str]:
    try:
        out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=20, check=True)
    except (OSError, subprocess.SubprocessError):
        return []
    return [ln for ln in out.stdout.splitlines() if ln.strip()]


def is_test_file(path: str) -> bool:
    return bool(re.search(r"(^|/)(tests?|spec|specs|__tests__)/|(^|/)test_[^/]*$|_test\.\w+$|\.(test|spec)\.\w+$", path))


def repo_facts(repo: pathlib.Path, text: str, cfg: Dict[str, Any], files: Optional[List[str]] = None,
               churn: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """Files the task names that exist in the repository, whether they have tests, how often they changed lately."""
    rc = cfg["repo"]
    files = files if files is not None else _git(repo, "ls-files")[: rc["max_files"]]
    if not files:
        return {"touched": [], "untested": [], "churned": []}
    tokens = set(re.findall(r"[\w./-]*[\w-]+\.[A-Za-z][\w]{0,5}\b", text)) | set(re.findall(r"`([\w./-]+/[\w./-]+)`", text))
    tokens = {t.strip("./") for t in tokens if len(t) > 3}
    by_name: Dict[str, List[str]] = {}
    for f in files:
        by_name.setdefault(f.rsplit("/", 1)[-1], []).append(f)
    touched = set()
    for t in tokens:
        if "/" in t:
            touched |= {f for f in files if f == t or f.endswith("/" + t)}
        else:
            hits = by_name.get(t, [])
            if len(hits) <= 3:          # a name found in many folders (index.ts, __init__.py) says too little
                touched |= set(hits)
    touched_l = sorted(touched)[:50]
    tests = [f for f in files if is_test_file(f)]
    untested = []
    code_ext = {e.lower() for e in rc.get("code_ext", [])}
    for f in touched_l:
        if is_test_file(f) or f.rsplit(".", 1)[-1].lower() not in code_ext:
            continue
        stem = f.rsplit("/", 1)[-1].split(".")[0].lower()
        if not any(stem in t.lower() for t in tests):
            untested.append(f)
    if churn is None:
        churn = {}
        if touched_l:
            for ln in _git(repo, "log", f"--since={rc['churn_days']}.days", "--name-only", "--format="):
                churn[ln] = churn.get(ln, 0) + 1
    churned = [f for f in touched_l if churn.get(f, 0) >= rc["churn_commits"]]
    return {"touched": touched_l, "untested": untested, "churned": churned}


def matches(paths: List[str], globs: List[str]) -> List[str]:
    return [p for p in paths if any(fnmatch.fnmatch(p, g) for g in globs)]


# ---- the score -----------------------------------------------------------------------------------------------

def score_text(text: str, cfg: Dict[str, Any], retry: int = 0, cause: str = "worker", labels: Optional[List[str]] = None,
               repo: Optional[pathlib.Path] = None, facts: Optional[Dict[str, Any]] = None,
               assessed: Optional[Dict[str, Any]] = None, cap: Optional[int] = None, cap_reason: str = "") -> Dict[str, Any]:
    body = task_body(text, cfg)
    assessed = assessed or {}
    neg = cfg["negation"]
    labels = [lb.lower() for lb in (labels or [])]
    lab = cfg["labels"]
    reasons: List[str] = []
    signals: List[str] = []
    points = 0

    sz = cfg["size"]
    size = stated_size(body, labels, cfg)
    size_source = "stated" if size != "?" else "unknown"
    size_note = ""
    if size == "?" and assessed.get("size") in sz["points"]:
        size, size_note, size_source = assessed["size"], " assessed", "assessed"
    sp = sz["points"].get(size, sz["default"])
    points += sp
    reasons.append(f"size {size}{size_note} (+{sp})")

    n = criteria_count(body, cfg)
    for at_least, pts in cfg["criteria"]["bands"]:
        if n >= at_least:
            points += pts
            reasons.append(f"{n} criteria (+{pts})")
            break

    dp = cfg["depends"]
    dm = re.search(dp["line"], body, re.I | re.M)
    deps = len(re.findall(dp["item"], dm.group(0))) if dm else 0
    if deps > dp["over"]:
        points += dp["points"]
        reasons.append(f"{deps} dependencies (+{dp['points']})")
        signals.append("dependencies")

    for sig in cfg["signals"]:
        if said(sig["pattern"], body, neg):
            points += sig["points"]
            reasons.append(f"{sig['name']} (+{sig['points']})")
            signals.append(sig["name"])

    if facts is None and repo is not None and cfg["repo"]["enabled"]:
        # files the assessor expects to change count as named by the task
        facts = repo_facts(repo, body + "\n" + "\n".join(assessed.get("files", [])), cfg)
    facts = facts or {"touched": [], "untested": [], "churned": []}
    rc = cfg["repo"]
    nt = len(facts["touched"])
    for at_least, pts in rc["files_bands"]:
        if nt >= at_least:
            points += pts
            reasons.append(f"touches {nt} files (+{pts})")
            signals.append("many files")
            break
    if facts["untested"]:
        points += rc["untested_points"]
        reasons.append(f"{len(facts['untested'])} touched file(s) without tests (+{rc['untested_points']})")
        signals.append("untested files")
    if facts["churned"]:
        points += rc["churn_points"]
        reasons.append(f"{len(facts['churned'])} often-changed file(s) (+{rc['churn_points']})")
        signals.append("often-changed files")

    step = 0
    for at_least, rung in cfg["thresholds"]:
        if points >= at_least:
            step = rung
            break

    # 1. critical: the line, a label, a configured path; otherwise a labelled word guess.
    crit_cfg = cfg["critical"]
    line = field(text, crit_cfg["line"])
    crit_paths = matches(facts["touched"], crit_cfg.get("paths", []))
    critical_guess = False
    if line is not None:
        critical, critical_source = line.lower().startswith("yes"), "line"
    elif any(lb in [c.lower() for c in lab["critical"]] for lb in labels):
        critical, critical_source = True, "label"
    elif crit_paths:
        critical, critical_source = True, "paths"
    else:
        # Words only guess: by default a guessed critical part is named, not acted on (`critical.guess_acts`).
        critical_guess = said(crit_cfg["guess"], body, neg)
        critical, critical_source = bool(critical_guess and crit_cfg.get("guess_acts")), "guessed"

    # 2. risk: the line, labels and configured paths act; words are only named.
    risk_cfg = cfg["risk"]
    rline = field(text, risk_cfg["line"])
    risk_words: List[str] = []
    risk: List[str] = []
    if rline is not None:
        risk = [w for w in re.split(r"[\s,]+", rline.lower()) if w in risk_cfg["kinds"]]
        risk_source = "line"
    else:
        for lb in labels:
            kind = {k.lower(): v for k, v in lab["risk"].items()}.get(lb)
            if kind and kind not in risk:
                risk.append(kind)
        for kind, globs in risk_cfg.get("paths", {}).items():
            if matches(facts["touched"], globs) and kind not in risk:
                risk.append(kind)
        risk_source = "labels or paths" if risk else "not set"
        if not risk:
            risk_words = [k for k, v in risk_cfg["kinds"].items() if said(v["pattern"], body, neg)]

    ladder = cfg["ladder"]
    if risk:
        if step < risk_cfg["floor"]:
            step = risk_cfg["floor"]
            r_model, r_effort = ladder[step]
            reasons.append(f"risk {', '.join(risk)} ({risk_source}): at least {r_model}, {r_effort}")
        else:
            reasons.append(f"risk {', '.join(risk)} ({risk_source})")
    if critical:
        step = len(ladder) - 1
        why = {"line": "", "label": " (critical label)", "paths": f" (touches {', '.join(crit_paths[:3])})",
               "guessed": f" (guessed: add **{crit_cfg['line']}:** yes or no)"}[critical_source]
        reasons.append("critical part changes: top of the ladder" + why)
    if critical_guess and not critical:
        reasons.append(f"the text may touch the critical part (add **{crit_cfg['line']}:** yes or no; a guess moves nothing)")
    if assessed.get("risk") and risk_source == "not set":
        reasons.append(f"the assessor suggests risk {', '.join(assessed['risk'])} (add **{risk_cfg['line']}:** to act on it)")
    if assessed.get("critical") and not critical:
        reasons.append(f"the assessor suggests the critical part changes (add **{crit_cfg['line']}:** yes to act on it)")
    if risk_source == "not set" and risk_words:
        reasons.append(f"risk not set; the text mentions {', '.join(risk_words)} "
                       f"(add **{risk_cfg['line']}:**; a guess moves nothing)")

    # A budget cap applies to first attempts of tasks that are neither critical nor risky.
    if cap is not None and not retry and not critical and not risk and step > cap:
        step = cap
        reasons.append(cap_reason or f"capped at {ladder[cap][0]}, {ladder[cap][1]}")

    # 4. retries: only the worker's own failures climb, and only up to the retry limit.
    # `retry` counts failures so far and `cause` describes the latest one: a failure that was not the worker's
    # earns no step, and the steps earlier failures earned are kept.
    stop = False
    if retry:
        jump = bool(cfg.get("retry_jumps_model"))
        limit = int(cfg.get("max_worker_retries", 0) or 0)
        earned = retry if cause == "worker" else retry - 1
        steps = min(earned, limit) if limit else earned
        for _ in range(steps):
            step = climb(step, ladder, jump)
        now_model, now_effort = ladder[min(step, len(ladder) - 1)]
        if cause == "worker":
            reasons.append(f"retry {retry}, the worker's own failures: {steps} step(s) up"
                           + (" (to the next model)" if jump else ""))
            if limit and retry > limit:
                stop = True
                reasons.append(f"retry limit ({limit}) reached: stop and ask a person")
        else:
            said_why = {"repeat": "the same failure on two different models, so it is unlikely to be the worker "
                                  "(check the environment and the task)",
                        "unknown": "the cause is not known (say whose failure it was)"}
            reasons.append(f"retry {retry}: " + said_why.get(cause, f"caused by the {cause} (fix the {cause} first)")
                           + f": stays on {now_model}, {now_effort}")
    model, effort = ladder[min(step, len(ladder) - 1)]

    # A task that only runs a tool (tests, lint, git status) can go to the cheapest rung, when switched on.
    mech = cfg.get("mechanical") or {}
    mechanical = bool(mech.get("enabled") and not retry and not critical and not risk and step == 0
                      and len(body.split()) <= mech["max_words"] and re.search(mech["patterns"], body, re.I)
                      and not re.search(mech["not_if"], body, re.I))
    if mechanical:
        model, effort = mech["rung"]
        reasons.append(f"mechanical task (it only runs a tool): {model}, {effort}")

    notes = readiness(body, cfg)
    stated = [size_source == "stated", critical_source != "guessed", risk_source != "not set"]
    basis = "stated" if all(stated) else ("partly stated" if any(stated) else "guessed")
    return {"score": points, "model": model, "effort": effort, "rung": min(step, len(ladder) - 1),
            "critical": critical, "critical_source": critical_source, "risk": risk, "risk_source": risk_source,
            "risk_words": risk_words, "signals": signals, "touched": facts["touched"], "reasons": reasons,
            "critical_guess": critical_guess, "size_source": size_source, "basis": basis, "mechanical": mechanical,
            "stop": stop,
            "ready": not notes, "readiness": notes}


def climb(step: int, ladder: List[List[str]], jump_model: bool = False) -> int:
    """The rung after a worker's own failure: the next one, or with jump_model the first rung of the next model
    (the benchmark found more effort on the same model rarely rescues a task that model failed)."""
    if step >= len(ladder) - 1:
        return len(ladder) - 1
    if not jump_model:
        return step + 1
    for k in range(step + 1, len(ladder)):
        if ladder[k][0] != ladder[step][0]:
            return k
    return step + 1


def score_task(task: Task, cfg: Dict[str, Any], retry: int = 0, cause: str = "worker",
               repo: Optional[pathlib.Path] = None, assessed: Optional[Dict[str, Any]] = None,
               cap: Optional[int] = None, cap_reason: str = "") -> Dict[str, Any]:
    return score_text(task.text, cfg, retry, cause, task.labels, repo, assessed=assessed, cap=cap, cap_reason=cap_reason)


# ---- the assessor (optional: one cheap model call) -----------------------------------------------------------

ASSESS_PROMPT = """You estimate a software task before a developer starts it. Do not change anything.
{look}
Answer with:
- size: XS (a line or two), S (one small change), M (a few functions or files), L (many files or a new feature),
  XL (a large feature or redesign): how big the CODE CHANGE will be, not how long the description is.
- files: the repository paths most likely to change (at most 8; empty if you cannot tell).
- risk: any of {kinds} that the change touches ({meanings}); empty if none.
- critical: true only if it changes {critical}.
- reason: one sentence.

<task>
{task}
</task>"""


def assess(text: str, cfg: Dict[str, Any], repo: Optional[pathlib.Path] = None, runner: Any = None) -> Dict[str, Any]:
    """Ask a cheap model (config `assess`) for the size, likely files, risk and critical estimate. Empty on failure."""
    ac = cfg["assess"]
    kinds = list(cfg["risk"]["kinds"])
    schema = {"type": "object", "required": ["size", "files", "risk", "critical", "reason"], "properties": {
        "size": {"type": "string", "enum": list(cfg["size"]["points"])},
        "files": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
        "risk": {"type": "array", "items": {"type": "string", "enum": kinds}},
        "critical": {"type": "boolean"}, "reason": {"type": "string"}}}
    look = ("Look at the repository in the current folder (Glob, Grep, Read) only as much as you need."
            if repo is not None and ac.get("read_repo", True) else "You cannot see the code; judge from the text.")
    prompt = ASSESS_PROMPT.format(look=look, kinds=", ".join(kinds), critical=ac.get("critical_meaning", "the core of the system"),
                                  meanings="; ".join(f"{k}: {v['meaning']}" for k, v in cfg["risk"]["kinds"].items()),
                                  task=text[: ac.get("max_chars", 12000)])
    tools = ["--allowedTools", "Read,Glob,Grep", "--max-turns", str(ac.get("max_turns", 8))] if "Glob" in look else \
            ["--tools", "", "--max-turns", "2"]
    cmd = [os.environ.get("CLAUDE_BIN") or "claude", "-p", prompt, "--model", ac["model"], "--effort", ac["effort"],
           "--output-format", "json", "--no-session-persistence", "--max-budget-usd", str(ac["max_budget_usd"]),
           "--json-schema", json.dumps(schema), "--safe-mode", "--strict-mcp-config", "--setting-sources", "",
           "--permission-mode", "default", "--permission-prompts", "none", *tools]
    try:
        run = runner or (lambda c, d: subprocess.run(c, cwd=d, capture_output=True, text=True, timeout=ac.get("timeout", 180)))
        out = json.loads(run(cmd, str(repo) if repo else None).stdout)
        got = out.get("structured_output") or {}
        if got.get("size") not in cfg["size"]["points"]:
            return {}
        got["risk"] = [k for k in got.get("risk", []) if k in kinds]
        got["cost_usd"] = out.get("total_cost_usd")
        return got
    except (OSError, ValueError, subprocess.SubprocessError, AttributeError):
        return {}


def choice_line(r: Dict[str, Any]) -> str:
    line = f"{MARK} `{r['model']}`, effort `{r['effort']}`. Score {r['score']}: {'; '.join(r['reasons'])}."
    if r.get("readiness"):
        line += f" Before starting: {'; '.join(r['readiness'])}."
    return line


def chosen_in(text: str) -> str:
    m = re.search(re.escape(MARK) + r" `(\w+)`, effort `(\w+)`", text)
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def write_choice(path: pathlib.Path, r: Dict[str, Any]) -> None:
    """Put the choice line under the first heading (or at the top), replacing an older one."""
    lines = path.read_text(encoding="utf-8").split("\n")
    for i, ln in enumerate(lines):
        if ln.startswith(MARK):
            del lines[i: i + 2 if i + 1 < len(lines) and lines[i + 1] == "" else i + 1]
            break
    at = 0
    if lines and lines[0].startswith("# "):
        at = 2 if len(lines) > 1 and lines[1] == "" else 1
        if at == 1:
            lines.insert(1, "")
            at = 2
    lines[at:at] = [choice_line(r), ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def check_env(env: Optional[Dict[str, str]] = None) -> List[str]:
    env = dict(os.environ) if env is None else env
    return [f"{k}={env[k]}: {why}" for k, why in OVERRIDES.items() if env.get(k)]


# ---- the outcome log -----------------------------------------------------------------------------------------

def read_log(path: pathlib.Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    rows: List[Dict[str, str]] = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        if not ln.startswith("| ") or ln.startswith("| Date "):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) == len(LOG_COLUMNS):
            rows.append(dict(zip(LOG_COLUMNS, cells)))
    return rows


def write_log(rows: List[Dict[str, str]], path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = ["# Model and effort log", "",
           "One row per finished task, written by `router.py record`, read by `router.py summary`. "
           "Chosen is the task's choice line; Ran is what the worker's session actually showed. "
           "Other failures are the environment's or the task's; they do not move a worker up.", "",
           "| " + " | ".join(LOG_COLUMNS) + " |", "|" + "---|" * len(LOG_COLUMNS)]
    for r in rows:
        out.append("| " + " | ".join(r.get(k, "").replace("|", "/") for k in LOG_COLUMNS) + " |")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def record(task: Task, cfg: Dict[str, Any], log: pathlib.Path, ran: str, first_try: str, retries: int = 0,
           other: str = "none", findings: int = 0, rework: int = 0, note: str = "",
           today: Optional[str] = None, repo: Optional[pathlib.Path] = None) -> Dict[str, str]:
    r = score_task(task, cfg, repo=repo)
    row = {"Date": today or datetime.date.today().isoformat(), "Task": task.name, "Score": str(r["score"]),
           "Critical": ("yes" if r["critical"] else "no") + ("?" if r["critical_source"] == "guessed" else ""),
           "Risk": ", ".join(r["risk"]) or ("none" if r["risk_source"] == "line" else "not set"),
           "Signals": ", ".join(r["signals"]) or "-", "Chosen": chosen_in(task.text) or f"{r['model']}/{r['effort']}",
           "Ran": ran, "First try": first_try, "Worker retries": str(retries), "Other failures": other or "none",
           "Review findings": str(findings), "Rework": str(rework), "Note": note or "-"}
    rows = [x for x in read_log(log) if x.get("Task") != task.name]
    rows.append(row)
    write_log(rows, log)
    return row


def _fails(group: List[Dict[str, str]]) -> int:
    return sum(r["First try"] != "pass" or int(r["Worker retries"] or 0) > 0 or int(r["Review findings"] or 0) > 0
               or int(r["Rework"] or 0) > 0 for r in group)


def suggestions(rows: List[Dict[str, str]], cfg: Dict[str, Any]) -> List[str]:
    """Point changes the log supports. A 'failure' is a failed first try, a worker retry, a review finding or rework."""
    out: List[str] = []
    n = len(rows)
    if n < 5:
        return out
    base = _fails(rows) / n
    points = {s["name"]: s["points"] for s in cfg["signals"]}
    names = sorted({s.strip() for r in rows for s in r["Signals"].split(",") if s.strip() not in ("", "-")})
    for nm in names:
        g = [r for r in rows if nm in [s.strip() for s in r["Signals"].split(",")]]
        if len(g) < 3:
            continue
        rate = _fails(g) / len(g)
        cur = points.get(nm)
        cur_s = f" (now {cur})" if cur is not None else ""
        if rate >= base + 0.25:
            out.append(f"raise '{nm}'{cur_s}: its tasks had trouble {_fails(g)}/{len(g)} times against {base:.0%} overall")
        elif rate == 0 and len(g) >= 5 and base > 0:
            out.append(f"lower '{nm}'{cur_s}: its {len(g)} tasks all went cleanly against {base:.0%} trouble overall")
    ladder = [f"{m}/{e}" for m, e in cfg["ladder"]]
    for i, rung in enumerate(ladder):
        g = [r for r in rows if r["Ran"] == rung]
        if len(g) < 5:
            continue
        rate = _fails(g) / len(g)
        if i > 0 and rate == 0:
            out.append(f"{rung} went cleanly on all {len(g)} tasks: try sending more to {ladder[i - 1]} "
                       "(raise the threshold that leads to it by 1)")
        if i < len(ladder) - 1 and rate >= 0.5:
            out.append(f"{rung} had trouble on {_fails(g)}/{len(g)} tasks: send more to {ladder[i + 1]} "
                       "(lower the threshold that leads to it by 1)")
    return out


def summary(rows: List[Dict[str, str]], cfg: Optional[Dict[str, Any]] = None) -> List[str]:
    out = [f"{len(rows)} tasks recorded."]
    if not rows:
        return out
    mism = [r for r in rows if r["Ran"] != r["Chosen"]]
    out.append(f"Ran on something other than chosen: {len(mism)}"
               + (f" ({', '.join(r['Task'] for r in mism)})" if mism else ""))

    def line(label: str, group: List[Dict[str, str]]) -> str:
        n = len(group)
        ok = sum(r["First try"] == "pass" for r in group)
        ret = sum(int(r["Worker retries"] or 0) for r in group)
        fnd = sum(int(r["Review findings"] or 0) for r in group)
        rw = sum(int(r["Rework"] or 0) for r in group)
        return f"  {label:<28} {n:>3} tasks, first try {ok}/{n}, worker retries {ret}, review findings {fnd}, rework {rw}"

    out.append("By what was chosen:")
    for ch in sorted({r["Chosen"] for r in rows}):
        out.append(line(ch, [r for r in rows if r["Chosen"] == ch]))
    out.append("By signal:")
    names = sorted({s.strip() for r in rows for s in r["Signals"].split(",") if s.strip() not in ("", "-")})
    for nm in names:
        out.append(line(nm, [r for r in rows if nm in [s.strip() for s in r["Signals"].split(",")]]))
    other = [r for r in rows if r["Other failures"] not in ("", "none")]
    out.append(f"Failures not caused by the worker (environment or task): {len(other)}")
    if len(rows) < 10:
        out.append("Fewer than 10 tasks: too few to change the points yet.")
    if cfg is not None:
        sug = suggestions(rows, cfg)
        out.append("Suggested changes (not applied; edit .claude/model-router.json):" if sug else "No change suggested.")
        out += [f"  - {s}" for s in sug]
    return out


# ---- savings: what model choice saved, from Claude Code's own usage records ------------------------------------

def price_of(model: str, prices: Dict[str, Any]) -> Optional[List[float]]:
    keys = sorted((k for k in prices if not k.startswith("_") and model.startswith(k)), key=len, reverse=True)
    return prices[keys[0]] if keys else None


def usage_cost(u: Dict[str, Any], price: List[float]) -> float:
    """Dollars for one reply's usage at a [input, output, cache read] price per million tokens."""
    pin, pout, pread = price
    cc = u.get("cache_creation") or {}
    w5 = cc.get("ephemeral_5m_input_tokens")
    w1 = cc.get("ephemeral_1h_input_tokens")
    if w5 is None and w1 is None:
        w5, w1 = u.get("cache_creation_input_tokens") or 0, 0
    return ((u.get("input_tokens") or 0) * pin + (u.get("output_tokens") or 0) * pout
            + (u.get("cache_read_input_tokens") or 0) * pread + (w5 or 0) * pin * 1.25 + (w1 or 0) * pin * 2.0) / 1e6


def read_usage(folders: List[pathlib.Path], since: str = "", until: str = "", mentions: str = "",
               exclude: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """One record per model reply (Claude Code repeats a reply's usage on every streamed block), from the JSONL
    transcripts under the folders. `mentions`: keep only sessions whose transcript contains that text."""
    files = sorted({f for d in folders for f in d.rglob("*.jsonl")})
    if since:
        # a file not touched since the day before `since` cannot hold a newer reply (a day's margin for time zones)
        try:
            floor = (datetime.datetime.strptime(since, "%Y-%m-%d") - datetime.timedelta(days=1)).timestamp()
            files = [f for f in files if f.stat().st_mtime >= floor]
        except (ValueError, OSError):
            pass
    keep_sessions: Optional[Set[str]] = None
    if mentions:
        keep_sessions = set()
        for f in files:
            try:
                if mentions in f.read_text(encoding="utf-8", errors="ignore"):
                    keep_sessions.add(f.stem if f.parent.name != "subagents" else f.parent.parent.name)
            except OSError:
                pass
    seen: Dict[str, Dict[str, Any]] = {}
    for f in files:
        session = f.stem if f.parent.name != "subagents" else f.parent.parent.name
        if (keep_sessions is not None and session not in keep_sessions) or session in (exclude or []):
            continue
        try:
            lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for ln in lines:
            if '"usage"' not in ln:
                continue
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            m = d.get("message") or {}
            u = m.get("usage")
            model = m.get("model") or ""
            day = (d.get("timestamp") or "")[:10]
            if not u or not model or model.startswith("<") or (since and day < since) or (until and day > until):
                continue
            key = m.get("id") or d.get("requestId") or d.get("uuid") or ln[:80]
            seen[key] = {"model": model, "usage": u, "day": day, "session": session,
                         "subagent": bool(d.get("isSidechain") or d.get("agentId") or f.parent.name == "subagents")}
    return list(seen.values())


def savings(records: List[Dict[str, Any]], cfg: Dict[str, Any], baseline: Optional[str] = None) -> Dict[str, Any]:
    """Estimated cost of every reply at its own model, and at the baseline model with the same tokens."""
    prices = cfg["prices"]
    base = baseline or cfg.get("savings_baseline", "claude-opus-5-5")
    bp = price_of(base, prices)
    if bp is None:
        raise ValueError(f"no price for the baseline {base}")
    rows: Dict[Tuple[str, str], Dict[str, Any]] = {}
    unpriced: Dict[str, int] = {}
    for r in records:
        p = price_of(r["model"], prices)
        if p is None:
            unpriced[r["model"]] = unpriced.get(r["model"], 0) + 1
            continue
        who = "subagents" if r["subagent"] else "main sessions"
        row = rows.setdefault((who, r["model"]), {"replies": 0, "tokens": 0, "cost": 0.0, "at_baseline": 0.0})
        u = r["usage"]
        row["replies"] += 1
        row["tokens"] += sum(int(u.get(k) or 0) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                                                            "cache_creation_input_tokens"))
        row["cost"] += usage_cost(u, p)
        row["at_baseline"] += usage_cost(u, bp)
    out: Dict[str, Any] = {"baseline": base, "replies": len(records), "sessions": len({r["session"] for r in records}),
                           "days": [min((r["day"] for r in records), default=""), max((r["day"] for r in records), default="")],
                           "rows": [{"who": k[0], "model": k[1], **v} for k, v in sorted(rows.items())], "unpriced": unpriced}
    for who in ("subagents", "main sessions", "all"):
        sel = [v for k, v in rows.items() if who == "all" or k[0] == who]
        cost, at = sum(v["cost"] for v in sel), sum(v["at_baseline"] for v in sel)
        out[who] = {"replies": sum(v["replies"] for v in sel), "tokens": sum(v["tokens"] for v in sel),
                    "cost": cost, "at_baseline": at, "saved": at - cost}
    return out


def savings_report(s: Dict[str, Any]) -> List[str]:
    def pct(a: float, b: float) -> str:
        return f"{a / b:.0%}" if b else "-"
    lines = [f"Estimated from Claude Code's own usage records: {s['replies']:,} model replies in {s['sessions']} sessions, "
             f"{s['days'][0]} to {s['days'][1]}. Prices: Anthropic API list prices per token (a subscription is billed "
             f"differently; read these as API-equivalent dollars). Baseline: the same tokens on {s['baseline']}.", "",
             "| Who | Model | Replies | Tokens | Estimated cost | Same tokens on the baseline |", "|---|---|---|---|---|---|"]
    for r in s["rows"]:
        lines.append(f"| {r['who']} | {r['model']} | {r['replies']:,} | {r['tokens']:,} | ${r['cost']:,.2f} | ${r['at_baseline']:,.2f} |")
    lines.append("")
    for who in ("subagents", "main sessions", "all"):
        v = s[who]
        word = "saved" if v["saved"] >= 0 else "cost more"
        lines.append(f"- **{who.capitalize()}:** ${v['cost']:,.2f} against ${v['at_baseline']:,.2f} on the baseline: "
                     f"{word} ${abs(v['saved']):,.2f} ({pct(abs(v['saved']), v['at_baseline'])}).")
    if s["unpriced"]:
        lines.append(f"- Not priced (add them to `prices`): {s['unpriced']}")
    lines += ["", "Same tokens on the baseline assumes the baseline model would have used the same number of tokens; "
              "a stronger model may need fewer turns, a weaker one more. Subagents are where the router chooses."]
    return lines


# ---- whose failure was it? (a suggestion from the failure output) ----------------------------------------------

def suggest_cause(log_text: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Suggest whose failure it was from a test run's or a worker's output. Environment evidence wins over task
    evidence, which wins over worker evidence: a model is only blamed when nothing else explains the failure."""
    found: Dict[str, List[str]] = {"environment": [], "task": [], "worker": []}
    for ln in log_text.splitlines()[-3000:]:
        line = ln.strip()
        if not line:
            continue
        for cause in ("environment", "task", "worker"):
            if any(re.search(pat, line, re.I) for pat in cfg["causes"][cause]):
                found[cause].append(line[:200])
                break
    cause = next((c for c in ("environment", "task", "worker") if found[c]), "unknown")
    return {"cause": cause, "counts": {c: len(v) for c, v in found.items()},
            "evidence": found[cause][:3] if cause != "unknown" else []}


def failure_fingerprint(log_text: str) -> str:
    """A short hash of what failed, the same for two runs that failed the same way: numbers, addresses and folder
    names are taken out, and only the lines that look like failures are kept."""
    lines = [ln for ln in log_text.splitlines() if re.search(r"error|fail|exception|assert|traceback|^E\s", ln, re.I)]
    text = "\n".join((lines or log_text.splitlines())[-200:])
    text = re.sub(r"[\w.~/\\:-]*[/\\]([\w.-]+)", r"\1", text)      # paths: keep the file name only
    text = re.sub(r"0x[0-9a-fA-F]+|\d+(\.\d+)?", "#", text)             # addresses, counts, timings
    return hashlib.sha256(re.sub(r"\s+", " ", text).strip().encode("utf-8")).hexdigest()[:16]


def note_failure(state_path: pathlib.Path, task_name: str, fingerprint: str, model: str,
                 today: Optional[str] = None) -> Dict[str, Any]:
    """Remember this failure and say whether the same one was seen before on this task (and on which models)."""
    try:
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    except (OSError, ValueError):
        state = {}
    history = state.setdefault("failures", {}).setdefault(task_name, [])
    same = [h for h in history if h.get("fingerprint") == fingerprint]
    out = {"seen_before": bool(same), "other_models": sorted({h.get("model", "") for h in same} - {model})}
    history.append({"fingerprint": fingerprint, "model": model, "day": today or datetime.date.today().isoformat()})
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=1), encoding="utf-8")
    except OSError:
        pass
    return out


# ---- audit: what the hook actually did ---------------------------------------------------------------------------

def audit(path: pathlib.Path) -> List[str]:
    """Read the hook's log back: how many subagent starts the router chose, kept or skipped."""
    if not path.exists():
        return [f"No hook log at {path}: the plugin's hook has not routed a subagent in this project "
                "(is the plugin installed and enabled here?)."]
    rows = []
    for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            rows.append(json.loads(ln))
        except ValueError:
            continue
    if not rows:
        return [f"The hook log at {path} is empty."]
    n = len(rows)
    routed = [r for r in rows if r.get("action") == "routed"]
    kept = [r for r in rows if str(r.get("action", "")).startswith("kept")]
    skipped = [r for r in rows if str(r.get("action", "")).startswith("skipped")]
    over = [r for r in routed if r.get("overridden_by")]
    out = [f"{n} subagent starts seen by the hook, {rows[0].get('time', '?')[:10]} to {rows[-1].get('time', '?')[:10]}.",
           f"- Routed by the router: {len(routed)} ({len(routed) / n:.0%})",
           f"- Kept as the caller set them (explicit model or effort): {len(kept)}",
           f"- Skipped (agent type in skip_agent_types): {len(skipped)}"]

    def tally(items: List[str]) -> str:
        counts: Dict[str, int] = {}
        for x in items:
            counts[x] = counts.get(x, 0) + 1
        return ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])) or "-"

    out.append("- The router chose: " + tally([f"{r.get('model')}/{r.get('effort')}" for r in routed]))
    if kept:
        out.append("- Kept choices were: " + tally([f"{r.get('model') or 'default'}/{r.get('effort') or 'default'}" for r in kept]))
    out.append("- By agent type: " + tally([r.get("agent_type") or "?" for r in rows]))
    capped = sum(1 for r in routed if any("budget" in x for x in r.get("reasons", [])))
    if capped:
        out.append(f"- Capped by the budget: {capped}")
    if over:
        out.append(f"- WARNING: {len(over)} routed start(s) ran while a machine setting overrides the choice "
                   f"({over[-1]['overridden_by'][0].split(':')[0]}): run check-env")
    return out


# ---- plug and play: one status screen, run data kept out of git --------------------------------------------------

def keep_out_of_git(cwd: pathlib.Path, names: List[str]) -> bool:
    """Tell git, on this machine only, to ignore the router's run data: lines in .git/info/exclude.

    The hook's log holds the first words of each subagent's task, so an `add -A` should not pick it up. Nothing
    tracked changes, a file that is already committed stays committed, and outside a git repository nothing happens."""
    found = _git(cwd, "rev-parse", "--git-path", "info/exclude")
    if not found:
        return False
    ex = pathlib.Path(found[0].strip())
    ex = ex if ex.is_absolute() else cwd / ex
    try:
        have = ex.read_text(encoding="utf-8", errors="ignore") if ex.exists() else ""
        missing = [n for n in names if n and n not in have.splitlines()]
        if missing:
            ex.parent.mkdir(parents=True, exist_ok=True)
            with ex.open("a", encoding="utf-8") as fh:
                fh.write(("" if not have or have.endswith("\n") else "\n")
                         + "# model-effort-router: run data of this machine\n" + "\n".join(missing) + "\n")
    except OSError:
        return False
    return True


def shared_root(here: pathlib.Path = HERE) -> Optional[pathlib.Path]:
    """The project this copy of the router is committed in (see `share`), or None for the plugin or a clone."""
    here = here.resolve()
    if here.name == "scripts" and here.parent.name == SHARED_DIR.name and here.parent.parent.name == ".claude":
        return here.parent.parent.parent
    return None


def project_copy(cwd: pathlib.Path, env: Optional[Dict[str, str]] = None) -> Optional[pathlib.Path]:
    """The project's root, when the project carries its own copy of the router and its settings run it.

    The plugin's hook stays silent in such a project: the project's copy routes, so nothing is routed or logged twice.
    Claude Code reads a project's settings only from the folder a session was started in (CLAUDE_PROJECT_DIR), so
    only that folder counts: a session started in a subfolder does not run the project's hooks, and the plugin
    routes it as usual."""
    env = os.environ if env is None else env
    root = pathlib.Path(env.get("CLAUDE_PROJECT_DIR") or cwd)
    try:
        if (root / SHARED_MARK).is_file() and SHARED_MARK in (root / PROJECT_SETTINGS).read_text(encoding="utf-8"):
            return root
    except OSError:
        pass
    return None


def shared_version(root: pathlib.Path) -> str:
    """The version of the copy a project carries, read from its router.py."""
    try:
        m = re.search(r'^VERSION = "([^"]+)"', (root / SHARED_DIR / "scripts" / "router.py").read_text(encoding="utf-8"), re.M)
    except OSError:
        return ""
    return m.group(1) if m else ""


def share(project: pathlib.Path, remove: bool = False, source: Optional[pathlib.Path] = None) -> Tuple[bool, List[str]]:
    """Put a copy of the router into a project and switch it on in the project's .claude/settings.json.

    Once both are committed, everyone who opens the project in Claude Code is routed: there is no plugin for them to
    install and nothing to update. The copy is the version you ran `share` from, and it stays that version until
    `share` is run again. `remove` takes the copy and the hook out. Other settings are kept; a settings file that is
    not valid JSON is left alone. Returns (ok, what happened)."""
    source = source or HERE.parent
    target, settings = project / SHARED_DIR, project / PROJECT_SETTINGS
    data: Any = {}
    if settings.exists():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict) or any(
                    not isinstance(v, list) for v in data.get("hooks", {}).values()):
                raise ValueError("it is not a JSON object with a `hooks` section of the usual shape")
        except ValueError as e:
            return False, [f"{settings} was not changed: {e}. Fix it and run this again."]

    def ours(g: Any) -> bool:
        return isinstance(g, dict) and any(isinstance(h, dict) and (SHARED_MARK in str(h.get("command", ""))
                                                                    or NO_PYTHON in str(h.get("command", "")))
                                           for h in g.get("hooks", []))

    def section(name: str) -> List[Any]:
        hooks = data.setdefault("hooks", {})
        if not isinstance(hooks, dict) or not isinstance(hooks.setdefault(name, []), list):
            raise ValueError("its `hooks` section has an unexpected shape")
        return hooks[name]

    if remove:
        hooks = data.get("hooks", {})
        had_hook = any(ours(g) for groups in hooks.values() for g in groups)
        had = target.exists() or had_hook
        if target.exists():
            shutil.rmtree(target)
        if had_hook:
            for name in list(hooks):
                hooks[name] = [g for g in hooks[name] if not ours(g)]
                if not hooks[name]:
                    del hooks[name]
            if not hooks:
                del data["hooks"]
            settings.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return True, ([f"Removed {SHARED_DIR}/ and its hooks from {PROJECT_SETTINGS}. Commit the change."] if had
                      else ["This project does not carry a copy of the router: nothing to remove."])
    if source.resolve() == target.resolve():
        return False, [f"This is the project's own copy ({SHARED_DIR}/). To update it, run `share` from a newer "
                       "model-effort-router (the plugin, or a clone of the repository)."]
    missing = [f for f in SHARED_FILES if not (source / f).is_file()]
    if missing:
        return False, [f"Cannot find {missing[0]} next to this script: nothing was copied."]
    before = shared_version(project)
    for f in SHARED_FILES:
        (target / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / f, target / f)
    (target / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    (target / "README.md").write_text(
        f"# model-effort-router {VERSION} (this project's copy)\n\n"
        "This folder was written by `router.py share`. It makes Claude Code choose the model and effort for every\n"
        "subagent started in this project, for everyone who opens the project, with nothing to install.\n"
        f"The hooks that run it are in `{PROJECT_SETTINGS.as_posix()}`.\n\n"
        "- Do not edit these files. This project's own rules go in `.claude/model-router.json`.\n"
        "- Needs Python 3.9 or later on each machine. Without it the hook does nothing.\n"
        "- Update: run `share` again from a newer model-effort-router. Remove: `share --remove`.\n"
        "- Check: `python3 .claude/model-effort-router/scripts/router.py status`\n\n"
        "Source and licence (MIT): https://github.com/islamkmansoori/model-effort-router\n", encoding="utf-8")
    cmd = hook_commands("${CLAUDE_PROJECT_DIR}/" + SHARED_MARK)
    for name, matcher, command, timeout in (("PreToolUse", "Agent", cmd["route"], 15),
                                            ("SessionStart", "startup", cmd["check"], 10)):
        groups = section(name)
        entry = {"matcher": matcher, "hooks": [{"type": "command", "command": command, "timeout": timeout}]}
        mine = [i for i, g in enumerate(groups) if ours(g)]
        if mine:
            groups[mine[0]] = entry
        else:
            groups.append(entry)
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    did = (f"Updated this project's copy from {before} to {VERSION}." if before and before != VERSION
           else f"This project's copy is {VERSION}." if before else f"Put model-effort-router {VERSION} into {SHARED_DIR}/.")
    return True, [did + f" Its hooks are in {PROJECT_SETTINGS}.",
                  f"Commit both: git add {SHARED_DIR.as_posix()} {PROJECT_SETTINGS.as_posix()}",
                  "From then on everyone who starts Claude Code in this folder is routed. They install nothing; "
                  "Claude Code asks them once whether they trust the project, as it does for any project with hooks. "
                  "Each machine needs Python 3.9 or later."]


def auto_update(cwd: pathlib.Path, home: Optional[pathlib.Path] = None) -> Optional[bool]:
    """Whether Claude Code updates the plugin by itself: `autoUpdate` on the marketplace, in the settings file that
    declares it (the project's local file first, then the project's, then the user's). None when none declares it."""
    declared = False
    for f in (cwd / ".claude" / "settings.local.json", cwd / PROJECT_SETTINGS,
              (home or pathlib.Path.home()) / ".claude" / "settings.json"):
        try:
            entry = (json.loads(f.read_text(encoding="utf-8")).get("extraKnownMarketplaces") or {}).get(MARKETPLACE)
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(entry, dict):
            declared = True
            if "autoUpdate" in entry:
                return entry["autoUpdate"] is True
    return False if declared else None


def status(cwd: pathlib.Path, cfg: Dict[str, Any], env: Optional[Dict[str, str]] = None) -> Tuple[List[str], bool]:
    """One screen: are the rules loaded, is the hook on, has it routed anything here, does anything override it."""
    hk = cfg["hook"]
    own_rules = (cwd / PROJECT_CONFIG).exists()
    out = [f"model-effort-router {VERSION}",
           f"- Rules: {PROJECT_CONFIG} (this project's own)" if own_rules
           else "- Rules: built in. Nothing to set up; `init` writes a copy to edit if you want other rules."]
    ladder = ", ".join("/".join(r) for r in cfg["ladder"])
    on = bool(hk.get("enabled", True))
    out.append(f"- Hook: on. Subagents get one of: {ladder}." if on
               else f"- Hook: OFF in {PROJECT_CONFIG} (`hook.enabled`). Subagents are not routed.")
    left = list(hk.get("skip_agent_types", [])) + (["calls that name their own model or effort"]
                                                   if hk.get("respect_explicit", True) else [])
    if on and left:
        out.append("- Left alone: " + ", ".join(left) + ".")
    found = check_env(env)
    out.append("- This machine: WARNING, a setting overrides the choice:" if found
               else "- This machine: no setting overrides the choice.")
    out += [f"    {f}" for f in found]
    rows: List[Dict[str, Any]] = []
    lp = cwd / hk["runs_log"]
    if lp.exists():
        for ln in lp.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                rows.append(json.loads(ln))
            except ValueError:
                continue
    routed = [r for r in rows if r.get("action") == "routed"]
    if rows:
        counts: Dict[str, int] = {}
        for r in routed:
            k = f"{r.get('model')}/{r.get('effort')}"
            counts[k] = counts.get(k, 0) + 1
        mix = ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])) or "none"
        out.append(f"- Working: yes. The hook has seen {len(rows)} subagent start(s) here and chose the model for "
                   f"{len(routed)} ({mix}); last on {str(rows[-1].get('time', '?'))[:10]}. Details: `audit`.")
    else:
        out.append("- Working: no subagent seen here yet. The first one writes the log. If subagents have already "
                   "run since the install, the plugin is not loaded: run `/reload-plugins` (or restart Claude Code) "
                   "and check that `/plugin` shows it as enabled.")
    copy = project_copy(cwd, {} if env is not None else None)
    if copy is not None:
        theirs = shared_version(copy)
        out.append(f"- Team: this project carries its own copy ({theirs or 'version unknown'}): everyone who opens it "
                   "is routed, with nothing to install."
                   + (f" This is {VERSION}: run `share` to bring the project's copy up to it."
                      if theirs and theirs != VERSION and shared_root() is None else ""))
    else:
        out.append("- Team: only your own install routes here. `share` puts a copy into the project, so that "
                   "everyone who opens it is routed with nothing to install.")
    auto = None if shared_root() is not None else auto_update(cwd)
    if shared_root() is not None:
        out.append("- Updates: the project's copy stays at this version until someone runs `share` again from a newer one.")
    elif auto is True:
        out.append("- Updates: automatic. Claude Code refreshes the plugin in the background.")
    elif auto is False:
        out.append(f"- Updates: by hand (`claude plugin update {PLUGIN_ID}`). To make them automatic, ask Claude to "
                   "switch on automatic updates for the router, or use `/plugin`, Marketplaces.")
    out.append(f"- Python: {sys.version.split()[0]}.")
    limits = {k: v for k, v in (cfg.get("budget") or {}).items() if k in ("daily", "weekly", "monthly") and v}
    if limits:
        out.append("- Budget: " + ", ".join(f"{k} ${v}" for k, v in limits.items()) + ". Spend so far: `budget`.")
    return out, on and not found


# ---- budget: spend against limits, from Claude Code's usage records -----------------------------------------------

def projects_dir_for(cwd: pathlib.Path) -> pathlib.Path:
    """Where Claude Code keeps the transcripts of sessions started in this folder."""
    return pathlib.Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(cwd))


def spend(records: List[Dict[str, Any]], cfg: Dict[str, Any]) -> float:
    total = 0.0
    for r in records:
        price = price_of(r["model"], cfg["prices"])
        if price:
            total += usage_cost(r["usage"], price)
    return total


def budget_status(folders: List[pathlib.Path], cfg: Dict[str, Any], today: Optional[datetime.date] = None,
                  records: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """API-equivalent spend today, in the last 7 days and this month, against the limits in the config."""
    b = cfg["budget"]
    day = today or datetime.date.today()
    starts = {"daily": day, "weekly": day - datetime.timedelta(days=6), "monthly": day.replace(day=1)}
    first = min(starts.values()).isoformat()
    recs = records if records is not None else read_usage(folders, since=first)
    out: Dict[str, Any] = {"periods": {}, "exceeded": False}
    for name, start in starts.items():
        spent = spend([r for r in recs if start.isoformat() <= r["day"] <= day.isoformat()], cfg)
        limit = float(b.get(name) or 0)
        level = "no limit"
        if limit:
            share = spent / limit
            reached = [a for a in sorted(b["alert_at"]) if share >= a]
            level = "ok" if not reached else ("EXCEEDED" if reached[-1] >= 1 else f"over {reached[-1]:.0%}")
            out["exceeded"] = out["exceeded"] or share >= 1
        out["periods"][name] = {"since": start.isoformat(), "spent": spent, "limit": limit, "level": level}
    return out


def budget_report(s: Dict[str, Any]) -> List[str]:
    names = {"daily": "Today", "weekly": "Last 7 days", "monthly": "This month"}
    lines = ["API-equivalent spend from Claude Code's usage records (a subscription is billed differently)."]
    for k, v in s["periods"].items():
        if v["limit"]:
            lines.append(f"- {names[k]}: ${v['spent']:,.2f} of ${v['limit']:,.2f} ({v['spent'] / v['limit']:.0%}): {v['level']}")
        else:
            lines.append(f"- {names[k]}: ${v['spent']:,.2f} (no limit set)")
    if not any(v["limit"] for v in s["periods"].values()):
        lines.append('Set limits in .claude/model-router.json, for example "budget": {"daily": 20, "monthly": 300}.')
    return lines


def budget_cap(cwd: pathlib.Path, cfg: Dict[str, Any], now: Optional[float] = None,
               folders: Optional[List[pathlib.Path]] = None) -> Optional[Dict[str, Any]]:
    """For the hook: the rung to cap ordinary tasks at when today's spend is past the throttle point, or None.
    Today's spend is kept in a small cache file so the hook does not re-read the transcripts on every call."""
    import time
    b = cfg["budget"]
    limit = float(b.get("daily") or 0)
    if not b.get("throttle") or not limit:
        return None
    now = time.time() if now is None else now
    day = datetime.date.fromtimestamp(now).isoformat()
    cache = cwd / ".claude" / "model-router-budget.json"
    spent: Optional[float] = None
    try:
        c = json.loads(cache.read_text(encoding="utf-8"))
        if c.get("day") == day and now - float(c.get("at", 0)) < float(b.get("cache_minutes", 10)) * 60:
            spent = float(c["spent"])
    except (OSError, ValueError, KeyError):
        pass
    if spent is None:
        spent = spend(read_usage(folders or [projects_dir_for(cwd)], since=day, until=day), cfg)
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"day": day, "spent": spent, "at": now}), encoding="utf-8")
        except OSError:
            pass
    if spent < float(b.get("throttle_at", 0.8)) * limit:
        return None
    rung = int(b.get("throttle_rung", 0))
    model, effort = cfg["ladder"][rung]
    return {"rung": rung, "reason": f"daily budget {spent / limit:.0%} used (${spent:,.2f} of ${limit:,.2f}): capped at "
                                    f"{model}, {effort} (critical and risky tasks, and retries, are not capped)"}


# ---- command line --------------------------------------------------------------------------------------------

def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(prog="router.py", description="Choose the Claude model and effort for a task.")
    ap.add_argument("--config", type=pathlib.Path, help="config file (default: .claude/model-router.json, else built-in)")
    ap.add_argument("--version", action="version", version=VERSION)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="write .claude/model-router.json with the default rules, to edit")
    sub.add_parser("status", help="one screen: is the router on, has it routed anything here, does anything override it")
    sh = sub.add_parser("share", help="put a copy of the router into this project, so everyone who opens it is "
                                      "routed with nothing to install")
    sh.add_argument("--remove", action="store_true", help="take the project's copy and its hook out again")
    s = sub.add_parser("score", help="score tasks")
    s.add_argument("tasks", nargs="*", help="task files, owner/repo#N issue references, or - for standard input")
    s.add_argument("--issue", action="append", default=[], help="a GitHub issue: owner/repo#123 or its URL")
    s.add_argument("--text", action="append", default=[], help="a plain prompt")
    s.add_argument("--repo", type=pathlib.Path, help="add facts from this git repository (files, tests, changes)")
    s.add_argument("--assess", nargs="?", const="always", choices=("always", "auto"),
                   help="ask a cheap model (config `assess`) to estimate size and files; "
                        "`auto` asks only when the task does not state its size")
    s.add_argument("--write", action="store_true", help="put the choice line into each task file")
    s.add_argument("--retry", type=int, default=0, help="rungs earned by failing its own tests twice")
    s.add_argument("--cause", choices=CAUSES, help="with --retry: whose failure (only worker moves up)")
    s.add_argument("--failure-log", type=pathlib.Path,
                   help="with --retry: the failing output; the cause is suggested from it when --cause is not "
                        "given, and the same failure on two models stops the climb")
    s.add_argument("--json", action="store_true")
    sub.add_parser("check-env", help="settings on this machine that would override the choice")
    rc = sub.add_parser("record", help="add a finished task's outcome to the log")
    rc.add_argument("task", help="the task file or owner/repo#N")
    rc.add_argument("--ran", required=True, help="model/effort the worker's session showed, e.g. sonnet/high")
    rc.add_argument("--first-try", required=True, choices=("pass", "fail"))
    rc.add_argument("--retries", type=int, default=0)
    rc.add_argument("--other", default="none", help="failures not caused by the worker, e.g. 'environment 2'")
    rc.add_argument("--findings", type=int, default=0, help="defects the review found")
    rc.add_argument("--rework", type=int, default=0, help="fixes needed after merge")
    rc.add_argument("--note", default="")
    rc.add_argument("--repo", type=pathlib.Path)
    sub.add_parser("summary", help="read the log back, with suggested point changes")
    cs = sub.add_parser("cause", help="suggest whose failure it was (worker, environment or task) from failing output")
    cs.add_argument("log", help="a file with the failing output, or - for standard input")
    cs.add_argument("--json", action="store_true")
    sub.add_parser("audit", help="what the hook did: how many subagent starts the router chose, kept or skipped")
    bg = sub.add_parser("budget", help="API-equivalent spend today, this week and this month against your limits")
    bg.add_argument("--projects-dir", type=pathlib.Path, action="append", default=[])
    bg.add_argument("--json", action="store_true")
    sv = sub.add_parser("savings", help="estimate what model choice saved, from Claude Code's usage records")
    sv.add_argument("--projects-dir", type=pathlib.Path, action="append", default=[],
                    help="transcript folder(s); default: this folder's under ~/.claude/projects")
    sv.add_argument("--since", default="", help="first day, YYYY-MM-DD")
    sv.add_argument("--until", default="", help="last day, YYYY-MM-DD")
    sv.add_argument("--mentions", default="", help="only sessions whose transcript contains this text")
    sv.add_argument("--baseline", help="model to compare with (default: config savings_baseline)")
    sv.add_argument("--exclude", action="append", default=[], help="a session id to leave out (repeatable)")
    sv.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        cfg = load_config(a.config)
    except ValueError as e:
        print(f"{a.config or PROJECT_CONFIG} is not valid JSON ({e}). Subagents are not routed until it is fixed; "
              "delete the file to go back to the built-in rules.")
        return 2
    log = pathlib.Path(cfg["log"])

    if a.cmd == "init":
        if PROJECT_CONFIG.exists():
            print(f"{PROJECT_CONFIG} already exists; not overwritten.")
            return 1
        PROJECT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        PROJECT_CONFIG.write_text(DEFAULT_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Wrote {PROJECT_CONFIG}. Edit the patterns, points, paths and ladder to fit this project.")
        return 0
    if a.cmd == "status":
        lines, fine = status(pathlib.Path.cwd(), cfg)
        print("\n".join(lines))
        return 0 if fine else 1
    if a.cmd == "share":
        ok, lines = share(pathlib.Path.cwd(), a.remove)
        print("\n".join(lines))
        return 0 if ok else 1
    if a.cmd == "check-env":
        found = check_env()
        print("\n".join(["Would override the chosen model or effort:", *found]) if found
              else "No model or effort override is set on this machine.")
        return 1 if found else 0
    if a.cmd == "summary":
        print("\n".join(summary(read_log(log), cfg)))
        return 0
    if a.cmd == "cause":
        text = sys.stdin.read() if a.log == "-" else pathlib.Path(a.log).read_text(encoding="utf-8", errors="ignore")
        sug = suggest_cause(text, cfg)
        if a.json:
            print(json.dumps(sug))
        else:
            c = sug["counts"]
            print(f"Suggested cause: {sug['cause']} (lines that point to the environment {c['environment']}, "
                  f"the task {c['task']}, the worker {c['worker']}).")
            for ev in sug["evidence"]:
                print(f"  {ev}")
            print({"environment": "Fix the environment and run again on the same model: a bigger model does not help.",
                   "task": "A person has to settle the task first: a bigger model does not help.",
                   "worker": "The change itself looks wrong: retry with --retry 1 --cause worker.",
                   "unknown": "Nothing recognised: read the output and say whose failure it was."}[sug["cause"]])
        return 0
    if a.cmd == "audit":
        lines = audit(pathlib.Path(cfg["hook"]["runs_log"]))
        print("\n".join(lines))
        return 0 if len(lines) > 1 else 1
    if a.cmd == "budget":
        dirs = a.projects_dir or [projects_dir_for(pathlib.Path.cwd())]
        missing = [d for d in dirs if not d.exists()]
        if missing:
            print(f"No transcripts at {missing[0]}; pass --projects-dir (a folder under ~/.claude/projects)")
            return 1
        st = budget_status(dirs, cfg)
        print(json.dumps(st, indent=1) if a.json else "\n".join(budget_report(st)))
        return 1 if st["exceeded"] else 0
    if a.cmd == "savings":
        dirs = a.projects_dir or [projects_dir_for(pathlib.Path.cwd())]
        missing = [d for d in dirs if not d.exists()]
        if missing:
            print(f"No transcripts at {missing[0]}; pass --projects-dir (a folder under ~/.claude/projects)")
            return 1
        res = savings(read_usage(dirs, a.since, a.until, a.mentions, a.exclude), cfg, a.baseline)
        print(json.dumps(res, indent=1) if a.json else "\n".join(savings_report(res)))
        return 0
    if a.cmd == "record":
        row = record(load_task(a.task), cfg, log, a.ran, a.first_try, a.retries, a.other, a.findings, a.rework,
                     a.note, repo=a.repo)
        print(" | ".join(f"{k}: {v}" for k, v in row.items()))
        if row["Ran"] != row["Chosen"]:
            print(f"WARNING: chosen {row['Chosen']} but ran {row['Ran']}: run check-env on that machine")
            return 1
        return 0

    if a.retry and not a.cause and not a.failure_log:
        ap.error("--retry needs --cause worker, environment or task, or --failure-log with the failing output: "
                 "only the worker's own failures move it up")
    tasks = [load_task(t) for t in a.tasks] + [fetch_issue(i) for i in a.issue] + \
            [Task(t, f"text {i + 1}") for i, t in enumerate(a.text)]
    if not tasks:
        ap.error("give a task file, --issue or --text")
    for t in tasks:
        want = a.assess == "always" or (a.assess == "auto" and
                                        stated_size(task_body(t.text, cfg), [lb.lower() for lb in t.labels], cfg) == "?")
        est = assess(t.text, cfg, a.repo) if want else None
        if want and not est:
            print(f"{t.name}: the assessor gave no answer; scoring without it", file=sys.stderr)
        cause = a.cause or "worker"
        notes: List[str] = []
        if a.retry and a.failure_log:
            failing = a.failure_log.read_text(encoding="utf-8", errors="ignore")
            failed_on = score_task(t, cfg, max(a.retry - 1, 0), "worker", a.repo, assessed=est)["model"]
            first_state = not pathlib.Path(cfg["state"]).exists()
            seen = note_failure(pathlib.Path(cfg["state"]), t.name, failure_fingerprint(failing), failed_on)
            if first_state and cfg.get("keep_run_data_out_of_git", True):
                keep_out_of_git(pathlib.Path.cwd(), [pathlib.Path(cfg["state"]).name])
            sug = suggest_cause(failing, cfg)
            if not a.cause:
                cause = sug["cause"]
                notes.append(f"cause suggested from the failure log: {cause}"
                             + (f" ({sug['evidence'][0][:80]})" if sug["evidence"] else ""))
            if seen["other_models"] and cause == "worker":
                cause = "repeat"
                notes.append(f"this exact failure was already seen on {', '.join(seen['other_models'])}")
        r = score_task(t, cfg, a.retry, cause, a.repo, assessed=est)
        r["reasons"] += notes
        if a.write and t.path is not None:
            write_choice(t.path, r)
        if a.json:
            print(json.dumps({"task": t.name, **r}))
        else:
            print(f"{t.name:<32} {r['model']:<6} {r['effort']:<6} score {r['score']:>2}  {'; '.join(r['reasons'])}")
            if r["readiness"]:
                print(f"{'':<32} before starting: {'; '.join(r['readiness'])}")
            if r["stop"]:
                print(f"{'':<32} STOP: the retry limit is reached; ask a person before spending more")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
