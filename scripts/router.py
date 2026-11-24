#!/usr/bin/env python3
"""model-effort-router: score a task file and choose the Claude model and effort a worker should run it on.

    python3 router.py init                                  # write .claude/model-router.json to edit
    python3 router.py score TASK.md [TASK2.md ...]          # one line per task
    python3 router.py score TASK.md --write                 # put the choice line into the task file
    python3 router.py score TASK.md --write --retry 1 --cause worker
    python3 router.py check-env                             # settings that would override the choice
    python3 router.py record TASK.md --ran sonnet/high --first-try pass
    python3 router.py summary                               # read the outcome log back

How it decides (every number lives in the config, not in this file):
  1. A task marked `**Critical:** yes` (it changes the part of the system where a mistake is most expensive)
     always gets the top rung of the ladder. Without that line the tool guesses from the text, skips sentences
     that say the critical part does NOT change, and labels the result "guessed".
  2. A task marked `**Risk:** money`, `security` or `data` gets at least the risk floor rung. A risk the tool
     only guesses from words is named but moves nothing, so loose word matches cannot raise cost.
  3. Otherwise by points: size, number of acceptance criteria, dependencies, and signals (schema, permissions,
     UI, open questions, shared files).
  4. Retries: only the worker's own failures (`--cause worker`) move it up a rung. A broken environment or an
     unclear task does not: a bigger model fixes neither.
  5. Record each finished task (`record`) and read the log back (`summary`) to tune the points from outcomes.

Standard library only. Python 3.9 or later.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import re
import sys
from typing import Any, Dict, List, Optional

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE.parent / "config" / "default.json"
PROJECT_CONFIG = pathlib.Path(".claude") / "model-router.json"
MARK = "**Model and effort (model-effort-router):**"
CAUSES = ("worker", "environment", "task")

# Settings that make Claude Code run on something other than what the router chose (Claude Code docs, Nov 2026).
OVERRIDES = {
    "CLAUDE_CODE_EFFORT_LEVEL": "overrides every other effort setting, including the Agent tool's effort and a subagent's `effort:`",
    "CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "makes subagents ignore the model they are given",
    "ANTHROPIC_MODEL": "sets a separate session's model when it is started without --model",
}

LOG_COLUMNS = ["Date", "Task", "Score", "Critical", "Risk", "Signals", "Chosen", "Ran", "First try",
               "Worker retries", "Other failures", "Review findings", "Rework", "Note"]


# ---- config --------------------------------------------------------------------------------------------------

def load_config(path: Optional[pathlib.Path] = None) -> Dict[str, Any]:
    """The project's .claude/model-router.json if there is one, else the bundled default."""
    cfg: Dict[str, Any] = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    p = path or PROJECT_CONFIG
    if p.exists():
        cfg.update(json.loads(p.read_text(encoding="utf-8")))
    return cfg


# ---- reading a task ------------------------------------------------------------------------------------------

def said(pattern: str, text: str, negation: str) -> bool:
    """True when a sentence matches the pattern and does not deny it (no, not, never, unchanged ...)."""
    for s in re.split(r"(?<=[.;:])\s+|\n", text):
        if re.search(pattern, s, re.I) and not re.search(negation, s, re.I):
            return True
    return False


def field(text: str, name: str) -> Optional[str]:
    m = re.search(rf"^\*\*{re.escape(name)}:\*\*\s*(.+)$", text, re.I | re.M)
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


def score_text(text: str, cfg: Dict[str, Any], retry: int = 0, cause: str = "worker") -> Dict[str, Any]:
    body = task_body(text, cfg)
    neg = cfg["negation"]
    reasons: List[str] = []
    signals: List[str] = []
    points = 0

    sz = cfg["size"]
    m = re.search(sz["pattern"], body, re.I)
    size = m.group(1).upper() if m else "?"
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
    m = re.search(dp["line"], body, re.I | re.M)
    deps = len(re.findall(dp["item"], m.group(0))) if m else 0
    if deps > dp["over"]:
        points += dp["points"]
        reasons.append(f"{deps} dependencies (+{dp['points']})")
        signals.append("dependencies")

    for sig in cfg["signals"]:
        if said(sig["pattern"], body, neg):
            points += sig["points"]
            reasons.append(f"{sig['name']} (+{sig['points']})")
            signals.append(sig["name"])

    step = 0
    for at_least, rung in cfg["thresholds"]:
        if points >= at_least:
            step = rung
            break

    # 1. critical part: the line decides; without it, a labelled guess.
    crit_cfg = cfg["critical"]
    line = field(text, crit_cfg["line"])
    if line is not None:
        critical, critical_source = line.lower().startswith("yes"), "line"
    else:
        critical, critical_source = said(crit_cfg["guess"], body, neg), "guessed"

    # 2. risk: the line decides; a guess is named only.
    risk_cfg = cfg["risk"]
    rline = field(text, risk_cfg["line"])
    risk_words: List[str] = []
    if rline is not None:
        risk = [w for w in re.split(r"[\s,]+", rline.lower()) if w in risk_cfg["kinds"]]
        risk_source = "line"
    else:
        risk, risk_source = [], "not set"
        risk_words = [k for k, v in risk_cfg["kinds"].items() if said(v["pattern"], body, neg)]

    ladder = cfg["ladder"]
    if risk:
        if step < risk_cfg["floor"]:
            step = risk_cfg["floor"]
            r_model, r_effort = ladder[step]
            reasons.append(f"risk {', '.join(risk)}: at least {r_model}, {r_effort}")
        else:
            reasons.append(f"risk {', '.join(risk)}")
    if critical:
        step = len(ladder) - 1
        reasons.append("critical part changes: top of the ladder"
                       + (f" (guessed: add **{crit_cfg['line']}:** yes or no)" if critical_source == "guessed" else ""))
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
    return {"score": points, "model": model, "effort": effort, "critical": critical,
            "critical_source": critical_source, "risk": risk, "risk_source": risk_source,
            "risk_words": risk_words, "signals": signals, "reasons": reasons}


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


def record(task: pathlib.Path, cfg: Dict[str, Any], log: pathlib.Path, ran: str, first_try: str, retries: int = 0,
           other: str = "none", findings: int = 0, rework: int = 0, note: str = "",
           today: Optional[str] = None) -> Dict[str, str]:
    text = task.read_text(encoding="utf-8")
    r = score_text(text, cfg)
    row = {"Date": today or datetime.date.today().isoformat(), "Task": task.name, "Score": str(r["score"]),
           "Critical": ("yes" if r["critical"] else "no") + ("?" if r["critical_source"] == "guessed" else ""),
           "Risk": ", ".join(r["risk"]) or ("none" if r["risk_source"] == "line" else "not set"),
           "Signals": ", ".join(r["signals"]) or "-", "Chosen": chosen_in(text) or f"{r['model']}/{r['effort']}",
           "Ran": ran, "First try": first_try, "Worker retries": str(retries), "Other failures": other or "none",
           "Review findings": str(findings), "Rework": str(rework), "Note": note or "-"}
    rows = [x for x in read_log(log) if x.get("Task") != task.name]
    rows.append(row)
    write_log(rows, log)
    return row


def summary(rows: List[Dict[str, str]]) -> List[str]:
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
    out.append("By signal (a signal whose tasks fail more often deserves more points; one that does not, fewer):")
    names = sorted({s.strip() for r in rows for s in r["Signals"].split(",") if s.strip() not in ("", "-")})
    for nm in names:
        out.append(line(nm, [r for r in rows if nm in [s.strip() for s in r["Signals"].split(",")]]))
    other = [r for r in rows if r["Other failures"] not in ("", "none")]
    out.append(f"Failures not caused by the worker (environment or task): {len(other)}")
    if len(rows) < 10:
        out.append("Fewer than 10 tasks: too few to change the points yet.")
    return out


# ---- command line --------------------------------------------------------------------------------------------

def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(prog="router.py", description="Choose the Claude model and effort for a task file.")
    ap.add_argument("--config", type=pathlib.Path, help="config file (default: .claude/model-router.json, else built-in)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="write .claude/model-router.json with the default rules, to edit")
    s = sub.add_parser("score", help="score task files")
    s.add_argument("tasks", nargs="+", type=pathlib.Path)
    s.add_argument("--write", action="store_true", help="put the choice line into each task file")
    s.add_argument("--retry", type=int, default=0, help="rungs earned by failing its own tests twice")
    s.add_argument("--cause", choices=CAUSES, help="with --retry: whose failure (only worker moves up)")
    s.add_argument("--json", action="store_true")
    sub.add_parser("check-env", help="settings on this machine that would override the choice")
    rc = sub.add_parser("record", help="add a finished task's outcome to the log")
    rc.add_argument("task", type=pathlib.Path)
    rc.add_argument("--ran", required=True, help="model/effort the worker's session showed, e.g. sonnet/high")
    rc.add_argument("--first-try", required=True, choices=("pass", "fail"))
    rc.add_argument("--retries", type=int, default=0)
    rc.add_argument("--other", default="none", help="failures not caused by the worker, e.g. 'environment 2'")
    rc.add_argument("--findings", type=int, default=0, help="defects the review found")
    rc.add_argument("--rework", type=int, default=0, help="fixes needed after merge")
    rc.add_argument("--note", default="")
    sub.add_parser("summary", help="read the log back")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    log = pathlib.Path(cfg["log"])

    if a.cmd == "init":
        if PROJECT_CONFIG.exists():
            print(f"{PROJECT_CONFIG} already exists; not overwritten.")
            return 1
        PROJECT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        PROJECT_CONFIG.write_text(DEFAULT_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Wrote {PROJECT_CONFIG}. Edit the patterns, points and ladder to fit this project.")
        return 0
    if a.cmd == "check-env":
        found = check_env()
        print("\n".join(["Would override the chosen model or effort:", *found]) if found
              else "No model or effort override is set on this machine.")
        return 1 if found else 0
    if a.cmd == "summary":
        print("\n".join(summary(read_log(log))))
        return 0
    if a.cmd == "record":
        row = record(a.task, cfg, log, a.ran, a.first_try, a.retries, a.other, a.findings, a.rework, a.note)
        print(" | ".join(f"{k}: {v}" for k, v in row.items()))
        if row["Ran"] != row["Chosen"]:
            print(f"WARNING: chosen {row['Chosen']} but ran {row['Ran']}: run check-env on that machine")
            return 1
        return 0

    if a.retry and not a.cause:
        ap.error("--retry needs --cause worker, environment or task: only the worker's own failures move it up")
    for t in a.tasks:
        r = score_text(t.read_text(encoding="utf-8"), cfg, a.retry, a.cause or "worker")
        if a.write:
            write_choice(t, r)
        if a.json:
            print(json.dumps({"task": str(t), **r}))
        else:
            print(f"{t.name:<32} {r['model']:<6} {r['effort']:<6} score {r['score']:>2}  {'; '.join(r['reasons'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
