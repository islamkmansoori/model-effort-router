#!/usr/bin/env python3
"""model-effort-router: score a task and choose the Claude model and effort a worker should run it on.

    python3 router.py init                                   # write .claude/model-router.json to edit
    python3 router.py score TASK.md [TASK2.md ...]           # a task file (story, packet, issue saved as markdown)
    python3 router.py score --issue owner/repo#123           # a GitHub issue (public, or with GITHUB_TOKEN)
    python3 router.py score --text "Fix the login timeout"   # a plain prompt; `-` reads standard input
    python3 router.py score TASK.md --repo .                 # add facts from the git repository
    python3 router.py score TASK.md --write                  # put the choice line into the task file
    python3 router.py score TASK.md --write --retry 1 --cause worker
    python3 router.py check-env                              # settings that would override the choice
    python3 router.py record TASK.md --ran sonnet/high --first-try pass
    python3 router.py summary                                # the outcome log, with suggested point changes

How it decides (every number lives in the config, not in this file):
  1. Critical: a `**Critical:** yes` line, a `critical` label, or a touched file under a configured critical path puts
     the task on the top rung. Without any of those the tool guesses from words, skips sentences that deny them,
     and labels the result "guessed".
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
import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.request
from typing import Any, Dict, List, Optional

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE.parent / "config" / "default.json"
PROJECT_CONFIG = pathlib.Path(".claude") / "model-router.json"
MARK = "**Model and effort (model-effort-router):**"
CAUSES = ("worker", "environment", "task")
VERSION = "0.2.0"

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
    """The project's .claude/model-router.json if there is one, else the bundled default. Top-level keys replace."""
    cfg: Dict[str, Any] = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    p = path or PROJECT_CONFIG
    if p.exists():
        cfg.update(json.loads(p.read_text(encoding="utf-8")))
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


def criteria_count(text: str, cfg: Dict[str, Any]) -> int:
    c = cfg["criteria"]
    parts = re.split(c["start"], text, maxsplit=1, flags=re.I)
    if len(parts) < 2:
        return 0
    body = re.split(c["end"], parts[1], maxsplit=1, flags=re.I | re.M)[0]
    return len(re.findall(c["item"], body, re.M))


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
               repo: Optional[pathlib.Path] = None, facts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    body = task_body(text, cfg)
    neg = cfg["negation"]
    labels = [lb.lower() for lb in (labels or [])]
    lab = cfg["labels"]
    reasons: List[str] = []
    signals: List[str] = []
    points = 0

    sz = cfg["size"]
    m = re.search(sz["pattern"], body, re.I)
    size = m.group(1).upper() if m else "?"
    if size == "?":
        for lb in labels:
            lm = re.match(lab["size"], lb, re.I)
            if lm:
                size = lm.group(1).upper()
                break
    sp = sz["points"].get(size, sz["default"])
    points += sp
    reasons.append(f"size {size} (+{sp})")

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
        facts = repo_facts(repo, body, cfg)
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
    if line is not None:
        critical, critical_source = line.lower().startswith("yes"), "line"
    elif any(lb in [c.lower() for c in lab["critical"]] for lb in labels):
        critical, critical_source = True, "label"
    elif crit_paths:
        critical, critical_source = True, "paths"
    else:
        critical, critical_source = said(crit_cfg["guess"], body, neg), "guessed"

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
    if risk_source == "not set" and risk_words:
        reasons.append(f"risk not set; the text mentions {', '.join(risk_words)} "
                       f"(add **{risk_cfg['line']}:**; a guess moves nothing)")

    # 4. retries: only the worker's own failures climb.
    if retry and cause == "worker":
        step += retry
        reasons.append(f"retry {retry}, the worker's own failures: {retry} rung(s) up")
    elif retry:
        reasons.append(f"retry {retry} caused by the {cause}: same model and effort (fix the {cause} first)")
    model, effort = ladder[min(step, len(ladder) - 1)]
    return {"score": points, "model": model, "effort": effort, "rung": min(step, len(ladder) - 1),
            "critical": critical, "critical_source": critical_source, "risk": risk, "risk_source": risk_source,
            "risk_words": risk_words, "signals": signals, "touched": facts["touched"], "reasons": reasons}


def score_task(task: Task, cfg: Dict[str, Any], retry: int = 0, cause: str = "worker",
               repo: Optional[pathlib.Path] = None) -> Dict[str, Any]:
    return score_text(task.text, cfg, retry, cause, task.labels, repo)


def choice_line(r: Dict[str, Any]) -> str:
    return f"{MARK} `{r['model']}`, effort `{r['effort']}`. Score {r['score']}: {'; '.join(r['reasons'])}."


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


# ---- command line --------------------------------------------------------------------------------------------

def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(prog="router.py", description="Choose the Claude model and effort for a task.")
    ap.add_argument("--config", type=pathlib.Path, help="config file (default: .claude/model-router.json, else built-in)")
    ap.add_argument("--version", action="version", version=VERSION)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="write .claude/model-router.json with the default rules, to edit")
    s = sub.add_parser("score", help="score tasks")
    s.add_argument("tasks", nargs="*", help="task files, owner/repo#N issue references, or - for standard input")
    s.add_argument("--issue", action="append", default=[], help="a GitHub issue: owner/repo#123 or its URL")
    s.add_argument("--text", action="append", default=[], help="a plain prompt")
    s.add_argument("--repo", type=pathlib.Path, help="add facts from this git repository (files, tests, changes)")
    s.add_argument("--write", action="store_true", help="put the choice line into each task file")
    s.add_argument("--retry", type=int, default=0, help="rungs earned by failing its own tests twice")
    s.add_argument("--cause", choices=CAUSES, help="with --retry: whose failure (only worker moves up)")
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
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    log = pathlib.Path(cfg["log"])

    if a.cmd == "init":
        if PROJECT_CONFIG.exists():
            print(f"{PROJECT_CONFIG} already exists; not overwritten.")
            return 1
        PROJECT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        PROJECT_CONFIG.write_text(DEFAULT_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Wrote {PROJECT_CONFIG}. Edit the patterns, points, paths and ladder to fit this project.")
        return 0
    if a.cmd == "check-env":
        found = check_env()
        print("\n".join(["Would override the chosen model or effort:", *found]) if found
              else "No model or effort override is set on this machine.")
        return 1 if found else 0
    if a.cmd == "summary":
        print("\n".join(summary(read_log(log), cfg)))
        return 0
    if a.cmd == "record":
        row = record(load_task(a.task), cfg, log, a.ran, a.first_try, a.retries, a.other, a.findings, a.rework,
                     a.note, repo=a.repo)
        print(" | ".join(f"{k}: {v}" for k, v in row.items()))
        if row["Ran"] != row["Chosen"]:
            print(f"WARNING: chosen {row['Chosen']} but ran {row['Ran']}: run check-env on that machine")
            return 1
        return 0

    if a.retry and not a.cause:
        ap.error("--retry needs --cause worker, environment or task: only the worker's own failures move it up")
    tasks = [load_task(t) for t in a.tasks] + [fetch_issue(i) for i in a.issue] + \
            [Task(t, f"text {i + 1}") for i, t in enumerate(a.text)]
    if not tasks:
        ap.error("give a task file, --issue or --text")
    for t in tasks:
        r = score_task(t, cfg, a.retry, a.cause or "worker", a.repo)
        if a.write and t.path is not None:
            write_choice(t.path, r)
        if a.json:
            print(json.dumps({"task": t.name, **r}))
        else:
            print(f"{t.name:<32} {r['model']:<6} {r['effort']:<6} score {r['score']:>2}  {'; '.join(r['reasons'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
