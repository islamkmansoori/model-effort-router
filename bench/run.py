#!/usr/bin/env python3
"""Benchmark: does model-effort-router pick a model and effort that solves real tasks at a lower cost?

Tasks are SWE-bench Verified instances (real GitHub issues with a human-checked fix and hidden tests). Each task
runs once on every rung of the ladder with Claude Code in headless mode. Then the report compares "always this rung",
"start at the bottom and climb on failure", the router's choice (alone, and climbing on failure), and the cheapest
rung that would have worked (known only in hindsight).

    python3 bench/run.py fetch                               # download the task list (SWE-bench Verified)
    python3 bench/run.py validate --set pilot                # free: the known fix passes, the setup works
    python3 bench/run.py route --set pilot                   # free: what the router chooses for each task
    python3 bench/run.py run --set pilot --run-budget 3 --total-budget 40 --jobs 2   # costs API money
    python3 bench/run.py report                              # results/REPORT.md and results/report.json

Needs: git, uv (https://docs.astral.sh/uv/), Claude Code (`claude`, or CLAUDE_BIN) and ANTHROPIC_API_KEY.
Runs use `--bare` (no user hooks, plugins, CLAUDE.md or memory), a per-run dollar cap, and tools limited to reading,
editing and running the task's own tests. Results append to results/runs.jsonl; a finished (task, rung) is not run
again, so an interrupted run resumes where it stopped.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
sys.path.insert(0, str(HERE))
import keyword_tiers  # noqa: E402
import router  # noqa: E402

DATA = HERE / "data"
RESULTS = HERE / "results"
# Working copies go to a folder without spaces: the allowed-tools rule names the venv's python by its path.
WORK = pathlib.Path(os.environ.get("BENCH_WORK") or (str(HERE / ".work") if " " not in str(HERE)
                    else os.path.join(__import__("tempfile").gettempdir(), "model-effort-router-bench")))
DATASET = DATA / "swe-bench-verified.json"
SETS = json.loads((HERE / "sets.json").read_text(encoding="utf-8"))
LADDER = [("sonnet", "medium"), ("sonnet", "high"), ("opus", "high"), ("opus", "xhigh")]

# How to build a working copy of each repository (pure-Python projects only: no Docker needed).
REPOS: Dict[str, Dict[str, Any]] = {
    "pytest-dev/pytest": {"python": "3.9", "install": ["-e", ".[testing]"], "scm": True},
    "pylint-dev/pylint": {"python": "3.9", "install": ["-e", ".", "pytest<8", "pytest-timeout"], "scm": False},
    "pallets/flask": {"python": "3.11", "install": ["-e", ".", "pytest<8"], "scm": False},
    "psf/requests": {"python": "3.9", "install": ["-e", ".", "pytest<8", "pytest-httpbin<2", "pytest-mock", "trustme"], "scm": False},
}

PROMPT = """You are fixing a GitHub issue in the repository in the current folder.

<issue>
{issue}
</issue>

Change the source code so the issue is fixed. Keep the change small and in the style of the code around it. Do not
edit, add or delete test files: hidden tests will check your fix. You may run tests with:
{python} -m pytest -x -q <test file or node id>
When the fix is done, stop and say in one sentence what you changed."""

_lock = threading.Lock()
AUTH = "api"   # set by --auth


def sh(cmd: List[str], cwd: Optional[pathlib.Path] = None, inp: Optional[str] = None, timeout: int = 1800,
       env: Optional[Dict[str, str]] = None) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(cmd, cwd=cwd, input=inp, capture_output=True, text=True, timeout=timeout,
                          env={**os.environ, **(env or {})})


def claude_bin() -> str:
    found = os.environ.get("CLAUDE_BIN") or shutil.which("claude")
    if not found:
        sys.exit("Claude Code not found: put `claude` on PATH or set CLAUDE_BIN")
    return found


# ---- tasks ---------------------------------------------------------------------------------------------------

def fetch() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    rows: List[Dict[str, Any]] = []
    for off in range(0, 500, 100):
        url = ("https://datasets-server.huggingface.co/rows?dataset=princeton-nlp/SWE-bench_Verified"
               f"&config=default&split=test&offset={off}&length=100")
        req = urllib.request.Request(url, headers={"User-Agent": "model-effort-router-bench"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            rows += [r["row"] for r in json.loads(resp.read().decode("utf-8"))["rows"]]
    DATASET.write_text(json.dumps(rows), encoding="utf-8")
    print(f"{len(rows)} tasks saved to {DATASET}")


def tasks(set_name: str, ids: List[str]) -> List[Dict[str, Any]]:
    if not DATASET.exists():
        fetch()
    rows = {r["instance_id"]: r for r in json.loads(DATASET.read_text(encoding="utf-8"))}
    want = ids or SETS[set_name]["ids"]
    missing = [i for i in want if i not in rows]
    if missing:
        sys.exit(f"not in SWE-bench Verified: {missing}")
    return [rows[i] for i in want]


def tests_of(task: Dict[str, Any], key: str) -> List[str]:
    v = task[key]
    return json.loads(v) if isinstance(v, str) else list(v)


# ---- working copies ------------------------------------------------------------------------------------------

def mirror(repo: str) -> pathlib.Path:
    m = WORK / "mirrors" / repo.replace("/", "__")
    with _lock:
        if not m.exists():
            m.parent.mkdir(parents=True, exist_ok=True)
            r = sh(["git", "clone", "--quiet", "--bare", f"https://github.com/{repo}.git", str(m)], timeout=1800)
            if r.returncode:
                raise RuntimeError(f"clone {repo}: {r.stderr[-400:]}")
    return m


def checkout(task: Dict[str, Any], label: str) -> pathlib.Path:
    """A fresh copy of the repository at the task's base commit, with its own virtual environment."""
    m = mirror(task["repo"])
    d = WORK / "runs" / f"{task['instance_id']}__{label}"
    if d.exists():
        with _lock:
            sh(["git", "-C", str(m), "worktree", "remove", "--force", str(d)])
        shutil.rmtree(d, ignore_errors=True)
    d.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        sh(["git", "-C", str(m), "worktree", "prune"])
        r = sh(["git", "-C", str(m), "worktree", "add", "--detach", "--force", str(d), task["base_commit"]])
    if r.returncode:
        raise RuntimeError(f"worktree {task['instance_id']}: {r.stderr[-400:]}")
    spec = REPOS[task["repo"]]
    venv = d.parent / (d.name + ".venv")
    shutil.rmtree(venv, ignore_errors=True)
    r = sh(["uv", "venv", "--quiet", "--python", spec["python"], str(venv)])
    if r.returncode:
        raise RuntimeError(f"venv: {r.stderr[-400:]}")
    env = {"SETUPTOOLS_SCM_PRETEND_VERSION": f"{task['version']}.0"} if spec["scm"] else {}
    r = sh(["uv", "pip", "install", "--quiet", "--python", str(venv / "bin" / "python"), *spec["install"]], cwd=d, env=env)
    if r.returncode:
        raise RuntimeError(f"install: {r.stderr[-600:]}")
    return d


def py(d: pathlib.Path) -> str:
    return str(d.parent / (d.name + ".venv") / "bin" / "python")


def apply(d: pathlib.Path, patch: str) -> bool:
    return sh(["git", "apply", "--whitespace=nowarn", "-"], cwd=d, inp=patch).returncode == 0


def patch_files(patch: str) -> List[str]:
    return re.findall(r"^diff --git a/(\S+) b/", patch, re.M)


def grade(task: Dict[str, Any], d: pathlib.Path, use_broken: bool = True) -> Dict[str, Any]:
    """Put the hidden tests in (test files back to the base commit first), run them, and say if all pass."""
    tp = task["test_patch"]
    files = patch_files(tp)
    sh(["git", "checkout", task["base_commit"], "--", *files], cwd=d)
    for f in files:                       # files the test patch adds must not exist yet
        p = d / f
        if p.exists() and sh(["git", "cat-file", "-e", f"{task['base_commit']}:{f}"], cwd=d).returncode:
            p.unlink()
    if not apply(d, tp):
        return {"resolved": False, "error": "test patch did not apply"}
    f2p, p2p = tests_of(task, "FAIL_TO_PASS"), tests_of(task, "PASS_TO_PASS")
    broken = set(env_broken().get(task["instance_id"], [])) if use_broken else set()
    p2p = [x for x in p2p if x not in broken]
    # Like SWE-bench: run the test files, then read each test's status (a node id that no longer exists would
    # otherwise stop the whole run).
    files = sorted({x.split("::")[0] for x in f2p + p2p})
    r = sh([py(d), "-m", "pytest", "-rA", "-p", "no:cacheprovider", "-q", *files], cwd=d, timeout=1800)
    passed = set(re.findall(r"^PASSED (\S.*?)(?: - .*)?$", r.stdout, re.M))
    f2p_ok = [t for t in f2p if t in passed]
    p2p_ok = [t for t in p2p if t in passed]
    return {"resolved": len(f2p_ok) == len(f2p) and len(p2p_ok) == len(p2p),
            "fail_to_pass": f"{len(f2p_ok)}/{len(f2p)}", "pass_to_pass": f"{len(p2p_ok)}/{len(p2p)}",
            "p2p_failed": [x for x in p2p if x not in passed]}


def env_broken() -> Dict[str, List[str]]:
    """Regression tests that fail on this machine even with the known fix (recorded by `validate`)."""
    p = RESULTS / "env-broken.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def cleanup(task: Dict[str, Any], d: pathlib.Path) -> None:
    m = mirror(task["repo"])
    with _lock:
        sh(["git", "-C", str(m), "worktree", "remove", "--force", str(d)])
    shutil.rmtree(d, ignore_errors=True)
    shutil.rmtree(d.parent / (d.name + ".venv"), ignore_errors=True)


# ---- commands ------------------------------------------------------------------------------------------------

def validate(ts: List[Dict[str, Any]]) -> int:
    """Free check: with only the hidden tests the task fails; with the known fix it passes."""
    bad = 0
    for t in ts:
        try:
            d = checkout(t, "validate")
            before = grade(t, d, use_broken=False)
            sh(["git", "checkout", "-f", t["base_commit"]], cwd=d)
            sh(["git", "clean", "-fdq"], cwd=d)
            ok = apply(d, t["patch"])
            after: Dict[str, Any] = grade(t, d, use_broken=False) if ok else {"resolved": False, "error": "gold patch did not apply"}
            f2p_all = str(after.get("fail_to_pass", "0/1")).split("/")
            broken: List[str] = list(after.get("p2p_failed", []))
            if broken and f2p_all[0] == f2p_all[1]:
                RESULTS.mkdir(parents=True, exist_ok=True)
                eb = env_broken()
                eb[t["instance_id"]] = broken
                (RESULTS / "env-broken.json").write_text(json.dumps(eb, indent=1), encoding="utf-8")
                print(f"    {len(broken)} regression test(s) cannot be graded here (they fail even with the known fix, or SWE-bench cut their name at a space): left out")
            good = (not before["resolved"]) and f2p_all[0] == f2p_all[1] and before.get("fail_to_pass", "").split("/")[0] != f2p_all[1]
            print(f"{'OK ' if good else 'BAD'} {t['instance_id']:<28} without fix: {before.get('fail_to_pass')}  "
                  f"with fix: {after.get('fail_to_pass')} / {after.get('pass_to_pass')} {after.get('error', '')}")
            bad += 0 if good else 1
            cleanup(t, d)
        except Exception as e:  # noqa: BLE001
            print(f"BAD {t['instance_id']:<28} {e}")
            bad += 1
    return 1 if bad else 0


ROUTE_FILES = {False: "routes.json", True: "routes-assessed.json"}


def route(ts: List[Dict[str, Any]], with_assessor: bool = False, reuse: bool = False) -> Dict[str, Dict[str, Any]]:
    """What the router chooses for each task: the issue text plus facts from the repository at the base commit.
    With the assessor, a cheap model also reads the issue and the code at that commit first (its cost is kept)."""
    cfg = router.load_config(pathlib.Path("/nonexistent"))
    out: Dict[str, Dict[str, Any]] = {}
    keywords: Dict[str, Dict[str, Any]] = {}
    sf = RESULTS / ROUTE_FILES[True]
    stored: Dict[str, Dict[str, Any]] = json.loads(sf.read_text(encoding="utf-8")) if reuse and sf.exists() else {}
    for t in ts:
        m = mirror(t["repo"])
        files = sh(["git", "-C", str(m), "ls-tree", "-r", "--name-only", t["base_commit"]]).stdout.split()
        est: Dict[str, Any] = {}
        prior = stored.get(t["instance_id"], {})
        if with_assessor and reuse and prior.get("assessed"):
            # the estimate the assessor already gave for this task, and what that call cost: no new model call
            est = {**prior["assessed"], "cost_usd": prior.get("assess_cost_usd", 0)}
        elif with_assessor:
            d = WORK / "assess" / t["instance_id"]
            with _lock:
                sh(["git", "-C", str(m), "worktree", "prune"])
                sh(["git", "-C", str(m), "worktree", "add", "--detach", "--force", str(d), t["base_commit"]])
            env_key = os.environ.pop("ANTHROPIC_API_KEY", None) if AUTH == "login" else None
            try:
                est = router.assess(t["problem_statement"], cfg, d)
            finally:
                if env_key:
                    os.environ["ANTHROPIC_API_KEY"] = env_key
                with _lock:
                    sh(["git", "-C", str(m), "worktree", "remove", "--force", str(d)])
        text = t["problem_statement"] + "\n" + "\n".join(est.get("files", []))
        facts = router.repo_facts(m, text, cfg, files=files, churn={})
        r = router.score_text(t["problem_statement"], cfg, facts=facts, assessed=est)
        out[t["instance_id"]] = {"rung": r["rung"], "model": r["model"], "effort": r["effort"], "score": r["score"],
                                 "reasons": r["reasons"], "assess_cost_usd": est.get("cost_usd", 0) if est else 0,
                                 "assessed": {k: est.get(k) for k in ("size", "files", "reason")} if est else None,
                                 "jump": bool(cfg.get("retry_jumps_model"))}
        # The keyword-tier baseline (bench/keyword_tiers.py): its top tier starts on Opus high, the rest at the bottom.
        kw_tier, kw_score = keyword_tiers.tier(t["problem_statement"])
        kw_rung = 2 if kw_tier == keyword_tiers.TIERS[-1] else 0
        keywords[t["instance_id"]] = {"rung": kw_rung, "model": LADDER[kw_rung][0], "effort": LADDER[kw_rung][1],
                                      "tier": kw_tier, "score": kw_score, "jump": True}
        print(f"{t['instance_id']:<28} {t['difficulty']:<16} {r['model']}/{r['effort']:<7} score {r['score']}: {'; '.join(r['reasons'])}")
    RESULTS.mkdir(parents=True, exist_ok=True)
    for f, new in ((RESULTS / ROUTE_FILES[with_assessor], out), (RESULTS / "routes-keywords.json", keywords)):
        old = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        f.write_text(json.dumps({**old, **new}, indent=1), encoding="utf-8")
    return out


def done_runs() -> Dict[str, Dict[str, Any]]:
    p = RESULTS / "runs.jsonl"
    if not p.exists():
        return {}
    out = {}
    for ln in p.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            r = json.loads(ln)
            out[f"{r['instance_id']}|{r['rung']}"] = r
    return out


def spent() -> float:
    return sum(float(r.get("cost_usd") or 0) for r in done_runs().values())


def one_run(t: Dict[str, Any], rung: int, run_budget: float, max_turns: int) -> Dict[str, Any]:
    model, effort = LADDER[rung]
    rec: Dict[str, Any] = {"instance_id": t["instance_id"], "repo": t["repo"], "difficulty": t["difficulty"],
                           "rung": rung, "model": model, "effort": effort}
    d = checkout(t, f"r{rung}")
    try:
        start = time.time()
        # api: --bare with ANTHROPIC_API_KEY. login: the user's Claude login, with every customization off.
        clean = ["--bare"] if AUTH == "api" else ["--safe-mode", "--strict-mcp-config", "--setting-sources", ""]
        cmd = [claude_bin(), "-p", PROMPT.format(issue=t["problem_statement"], python=py(d)), *clean,
               "--model", model, "--effort", effort, "--output-format", "json", "--no-session-persistence",
               "--max-budget-usd", str(run_budget), "--max-turns", str(max_turns),
               "--permission-mode", "acceptEdits", "--permission-prompts", "none",
               "--allowedTools", f"Read,Edit,Write,Glob,Grep,Bash({py(d)} -m pytest *)"]
        env = dict(os.environ)
        if AUTH == "login":
            env.pop("ANTHROPIC_API_KEY", None)
        r = subprocess.run(cmd, cwd=d, capture_output=True, text=True, timeout=3600, env=env)
        rec["seconds"] = round(time.time() - start)
        try:
            out = json.loads(r.stdout)
        except json.JSONDecodeError:
            out = {"is_error": True, "result": (r.stdout or r.stderr)[-300:]}
        rec.update({"cost_usd": out.get("total_cost_usd"), "turns": out.get("num_turns"), "agent_error": out.get("is_error"),
                    "subtype": out.get("subtype"), "models_used": sorted((out.get("modelUsage") or {}).keys()),
                    "usage": out.get("usage")})
        rec["diff_lines"] = len(sh(["git", "diff"], cwd=d).stdout.splitlines())
        rec.update(grade(t, d))
    finally:
        cleanup(t, d)
    return rec


def run(ts: List[Dict[str, Any]], rungs: List[int], run_budget: float, total_budget: float, jobs: int,
        max_turns: int) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    done = done_runs()
    todo = [(t, k) for t in ts for k in rungs if f"{t['instance_id']}|{k}" not in done]
    print(f"{len(todo)} runs to do; spent so far ${spent():.2f}; stop at ${total_budget:.2f}")

    def work(item: Any) -> None:
        t, k = item
        if spent() + run_budget > total_budget:
            print(f"skip {t['instance_id']} rung {k}: the total budget would be exceeded")
            return
        free_gb = shutil.disk_usage(str(WORK.parent if WORK.parent.exists() else HERE)).free / 1e9
        if free_gb < 5:
            print(f"skip {t['instance_id']} rung {k}: only {free_gb:.1f} GB free on disk (need 5)")
            return
        try:
            rec = one_run(t, k, run_budget, max_turns)
        except Exception as e:  # noqa: BLE001
            rec = {"instance_id": t["instance_id"], "rung": k, "difficulty": t["difficulty"], "model": LADDER[k][0],
                   "effort": LADDER[k][1], "resolved": False, "cost_usd": 0, "setup_error": str(e)[-400:]}
        with _lock:
            with (RESULTS / "runs.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
        print(f"{rec['instance_id']:<28} {rec['model']}/{rec['effort']:<7} resolved={rec.get('resolved')} "
              f"${float(rec.get('cost_usd') or 0):.2f} {rec.get('seconds', '-')}s {rec.get('setup_error', '')}", flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as ex:
        list(ex.map(work, todo))
    print(f"spent ${spent():.2f}")


# ---- the report ----------------------------------------------------------------------------------------------

def strategies(runs: Dict[str, Dict[str, Any]], routes: Dict[str, Dict[str, Dict[str, Any]]], ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Resolved tasks and total cost for each way of choosing, over the tasks that ran on every rung."""
    def res(i: str, k: int) -> Dict[str, Any]:
        return runs[f"{i}|{k}"]

    def cost(i: str, k: int) -> float:
        return float(res(i, k).get("cost_usd") or 0)

    def ok(i: str, k: int) -> bool:
        return bool(res(i, k).get("resolved"))

    def climb(i: str, start: int, jump: bool = False) -> Tuple[bool, float]:
        c, k = 0.0, start
        while True:
            c += cost(i, k)
            if ok(i, k):
                return True, c
            if k >= len(LADDER) - 1:
                return False, c
            k = router.climb(k, [list(x) for x in LADDER], jump)

    out: Dict[str, Dict[str, Any]] = {}
    for k, (m, e) in enumerate(LADDER):
        out[f"Always {m} {e}"] = {"solved": sum(ok(i, k) for i in ids), "cost": sum(cost(i, k) for i in ids)}
    s = [climb(i, 0) for i in ids]
    out["Start at the bottom, climb on failure"] = {"solved": sum(x[0] for x in s), "cost": sum(x[1] for x in s)}
    s = [climb(i, 0, True) for i in ids]
    out["Start at the bottom, jump to the next model on failure"] = {"solved": sum(x[0] for x in s), "cost": sum(x[1] for x in s)}
    for label, rt in routes.items():
        if not all(i in rt for i in ids):
            continue
        extra = sum(float(rt[i].get("assess_cost_usd") or 0) for i in ids)
        out[label] = {"solved": sum(ok(i, rt[i]["rung"]) for i in ids),
                      "cost": sum(cost(i, rt[i]["rung"]) for i in ids) + extra}
        jump = bool(rt[ids[0]].get("jump", False))
        s = [climb(i, rt[i]["rung"], jump) for i in ids]
        out[f"{label}, {'jump' if jump else 'climb'} on failure"] = {"solved": sum(x[0] for x in s),
                                                                     "cost": sum(x[1] for x in s) + extra}
    best = []
    for i in ids:
        wins = [cost(i, k) for k in range(len(LADDER)) if ok(i, k)]
        best.append((bool(wins), min(wins) if wins else 0.0))
    out["Cheapest rung that worked (hindsight)"] = {"solved": sum(x[0] for x in best), "cost": sum(x[1] for x in best)}
    return out


def rung_for(score: int, critical: bool, thresholds: List[List[int]]) -> int:
    if critical:
        return len(LADDER) - 1
    for at_least, rung in thresholds:
        if score >= at_least:
            return rung
    return 0


def halves(ids: List[str]) -> Tuple[List[str], List[str]]:
    """A fixed split: every other task (sorted by id) tunes, the rest tests. Nothing random, so anyone gets the same."""
    s = sorted(ids)
    return s[0::2], s[1::2]


def tune() -> int:
    """Try starting rules and climbing styles on the tuning half; score the best on the test half it never saw."""
    runs = done_runs()
    ids = sorted({r["instance_id"] for r in runs.values() if all(f"{r['instance_id']}|{k}" in runs for k in range(len(LADDER)))})
    if len(ids) < 6:
        print("Too few complete tasks to tune.")
        return 1
    tune_ids, test_ids = halves(ids)
    base = {lbl: json.loads((RESULTS / f).read_text(encoding="utf-8")) for lbl, f in
            (("rules", "routes.json"), ("assessor", "routes-assessed.json")) if (RESULTS / f).exists()}
    cands: List[Dict[str, Any]] = []
    for src, rt in base.items():
        if not all(i in rt for i in ids):
            continue
        for cut in range(0, 6):
            for low in (0, 1, 2):
                for jump in (False, True):
                    th = [[cut, 2], [0, low]] if low < 2 else [[0, 2]]
                    routes = {i: {"rung": rung_for(rt[i]["score"], any("critical part changes" in x for x in rt[i]["reasons"]), th),
                                  "assess_cost_usd": rt[i].get("assess_cost_usd", 0), "jump": jump} for i in ids}
                    cands.append(dict(source=src, thresholds=th, jump=jump, routes=routes))
    def score_on(c: Dict[str, Any], part: List[str]) -> Dict[str, Any]:
        st = strategies(runs, {"x": c["routes"]}, part)
        key = [k for k in st if k.startswith("x, ")][0]
        return st[key]
    seen = set()
    uniq = []
    for c in cands:
        sig = (c["source"], c["jump"], tuple(c["routes"][i]["rung"] for i in ids))
        if sig not in seen:
            seen.add(sig)
            uniq.append(c)
    best = min(uniq, key=lambda c: (-score_on(c, tune_ids)["solved"], score_on(c, tune_ids)["cost"]))
    jump_default = bool(router.load_config(pathlib.Path("/nonexistent")).get("retry_jumps_model"))
    default = {i: {**v, "jump": jump_default} for i, v in base.get("rules", {}).items()}
    tuned_test = score_on(best, test_ids)
    out = {"tune_ids": tune_ids, "test_ids": test_ids, "candidates": len(uniq),
           "chosen": {"source": best["source"], "thresholds": best["thresholds"], "retry_jumps_model": best["jump"]},
           "tuned_on_tune_half": score_on(best, tune_ids), "tuned_on_test_half": tuned_test,
           "test_half_baselines": strategies(runs, {"Router (default rules)": default} if default else {}, test_ids)}
    (RESULTS / "tuning.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k not in ("tune_ids", "test_ids")}, indent=1))
    return 0


def md_to_html(lines: List[str]) -> str:
    """The few Markdown forms the report uses (paragraphs, tables, **bold**, `code`, links) as HTML."""
    import html as h

    def inline(s: str) -> str:
        s = h.escape(s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
        return re.sub(r"\[(.+?)\]\((.+?)\)", lambda m: f'<a href="https://github.com/islamkmansoori/model-effort-router/blob/main/{m.group(2)}">{m.group(1)}</a>', s)

    out: List[str] = []
    table: List[List[str]] = []
    for ln in lines + [""]:
        if ln.startswith("|"):
            if not re.match(r"^\|[-|]+\|$", ln):
                table.append([c.strip() for c in ln.strip().strip("|").split("|")])
            continue
        if table:
            out.append("<table>" + "".join(("<tr>" + "".join(f"<{'th' if i == 0 else 'td'}>{inline(c)}</{'th' if i == 0 else 'td'}>" for c in row) + "</tr>")
                                           for i, row in enumerate(table)) + "</table>")
            table = []
        if ln.strip():
            out.append(f"<p>{inline(ln)}</p>")
    return "\n".join(out)


def report() -> int:
    runs = done_runs()
    routes: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for label, fname in (("Router (rules only)", "routes.json"), ("Router (with assessor)", "routes-assessed.json"),
                         ("Keyword tiers (top tier starts on Opus)", "routes-keywords.json")):
        if (RESULTS / fname).exists():
            routes[label] = json.loads((RESULTS / fname).read_text(encoding="utf-8"))
    ids = sorted({r["instance_id"] for r in runs.values()
                  if all(f"{r['instance_id']}|{k}" in runs for k in range(len(LADDER)))})
    if not ids:
        print("No task has run on every rung yet.")
        return 1
    st = strategies(runs, routes, ids)
    diff = {i: next(r["difficulty"] for r in runs.values() if r["instance_id"] == i) for i in ids}
    lines = ["# Benchmark results", "",
             f"{len(ids)} SWE-bench Verified tasks, each run once on every rung with Claude Code in headless mode "
             "(`--bare` or `--safe-mode`: no user customizations; per-run budget cap). Solved = every hidden test passes. Cost = Claude Code's own "
             "`total_cost_usd` estimate. One run per task and rung: small numbers, read them as a pilot.", "",
             "| Way of choosing | Tasks solved | Total cost | Cost per solved task |", "|---|---|---|---|"]
    for name, v in st.items():
        per = f"${v['cost'] / v['solved']:.2f}" if v["solved"] else "-"
        lines.append(f"| {name} | {v['solved']}/{len(ids)} | ${v['cost']:.2f} | {per} |")
    lines += ["", "Router costs include the assessor's own model call. Climb on failure = if the chosen rung fails, run the "
              "next one up, paying for every attempt; jump = go straight to the next model. Keyword tiers is a "
              "the four-tier keyword scorer of an earlier project by the same author (`bench/keyword_tiers.py`).", "",
              "## Each task", "", "| Task | Difficulty | Router chose (rules / assessor) | " + " | ".join(f"{m} {e}" for m, e in LADDER) + " |",
              "|---|---|---|" + "---|" * len(LADDER)]
    for i in ids:
        cells = []
        for k in range(len(LADDER)):
            r = runs[f"{i}|{k}"]
            cells.append(f"{'solved' if r.get('resolved') else 'not solved'} ${float(r.get('cost_usd') or 0):.2f}")
        ch = " / ".join(f"{rt[i]['model']} {rt[i]['effort']}" if i in rt else "-"
                        for name, rt in routes.items() if name.startswith("Router")) or "-"
        lines.append(f"| {i} | {diff[i]} | {ch} | " + " | ".join(cells) + " |")
    tp = RESULTS / "tuning.json"
    if tp.exists():
        tu = json.loads(tp.read_text(encoding="utf-8"))
        ch = tu["chosen"]
        n = len(tu["test_ids"])
        lines += ["", "## Tuned on one half, tested on the other", "",
                  f"The {len(tu['tune_ids']) + n} tasks are split in a fixed way (every other task by id). {tu['candidates']} starting "
                  "rules and climbing styles were tried on the tuning half only; the best is scored here on the test half, "
                  f"which it never saw. Chosen: router with {'the assessor' if ch['source'] == 'assessor' else 'rules only'}, "
                  f"thresholds {ch['thresholds']} (points to rung), "
                  f"{'jump to the next model' if ch['retry_jumps_model'] else 'climb one rung'} on failure.", "",
                  "| Way of choosing (test half) | Tasks solved | Total cost | Cost per solved task |", "|---|---|---|---|"]
        rows = dict(tu["test_half_baselines"])
        rows["**Tuned router (chosen on the other half)**"] = tu["tuned_on_test_half"]
        for name, v in rows.items():
            per = f"${v['cost'] / v['solved']:.2f}" if v["solved"] else "-"
            lines.append(f"| {name} | {v['solved']}/{n} | ${v['cost']:.2f} | {per} |")
    errs = [r for r in runs.values() if r.get("setup_error")]
    if errs:
        lines += ["", f"Setup errors (counted as not solved): {len(errs)}."]
    (RESULTS / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    readme = HERE.parent / "README.md"
    if readme.exists():
        # The README shows the summary tables (everything before the per-task table, plus the tuning section).
        cut = lines.index("## Each task")
        block = lines[2:cut]
        if "## Tuned on one half, tested on the other" in lines:
            i = lines.index("## Tuned on one half, tested on the other")
            block += ["**Tuned on one half, tested on the other.**"] + [x for x in lines[i + 1:] if not x.startswith("Setup errors")]
        block += ["", "Per task, with every run's cost: [bench/results/REPORT.md](bench/results/REPORT.md)."]
        text = readme.read_text(encoding="utf-8")
        a, b = "<!-- results:start -->", "<!-- results:end -->"
        if a in text and b in text:
            text = text[: text.index(a) + len(a)] + "\n" + "\n".join(block).strip() + "\n" + text[text.index(b):]
            readme.write_text(text, encoding="utf-8")
        page = HERE.parent / "docs" / "index.html"
        if page.exists():
            html = page.read_text(encoding="utf-8")
            if a in html and b in html:
                html = html[: html.index(a) + len(a)] + "\n" + md_to_html(block) + "\n" + html[html.index(b):]
                page.write_text(html, encoding="utf-8")
    (RESULTS / "report.json").write_text(json.dumps({"tasks": ids, "strategies": st}, indent=1), encoding="utf-8")
    print("\n".join(lines))
    return 0


def main(argv: List[str]) -> int:
    global AUTH
    ap = argparse.ArgumentParser(prog="bench/run.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch")
    for name in ("validate", "route", "run"):
        p = sub.add_parser(name)
        p.add_argument("--set", default="pilot", choices=sorted(SETS))
        p.add_argument("--ids", nargs="*", default=[])
        if name == "route":
            p.add_argument("--assess", action="store_true", help="also ask the assessor (a cheap model call per task)")
            p.add_argument("--reuse-assessed", action="store_true",
                           help="with --assess: reuse the estimates already stored in results/ (no model calls)")
            p.add_argument("--auth", choices=("api", "login"), default="api")
        if name == "run":
            p.add_argument("--rungs", default="0,1,2,3")
            p.add_argument("--run-budget", type=float, default=3.0, help="dollar cap per run")
            p.add_argument("--total-budget", type=float, required=True, help="stop starting runs past this total")
            p.add_argument("--jobs", type=int, default=2)
            p.add_argument("--max-turns", type=int, default=60)
            p.add_argument("--auth", choices=("api", "login"), default="api",
                           help="api: --bare with ANTHROPIC_API_KEY (billed); login: your Claude login in safe mode "
                                "(uses your plan's limits)")
    sub.add_parser("report")
    sub.add_parser("tune", help="tune on half the tasks, test on the other half (free: uses finished runs)")
    a = ap.parse_args(argv)
    if a.cmd == "fetch":
        fetch()
        return 0
    if a.cmd == "report":
        return report()
    if a.cmd == "tune":
        return tune()
    ts = tasks(a.set, a.ids)
    if a.cmd == "validate":
        return validate(ts)
    if a.cmd == "route":
        AUTH = a.auth
        route(ts, a.assess, a.reuse_assessed)
        return 0
    AUTH = a.auth
    if AUTH == "api" and not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set (--auth api runs need it); or use --auth login")
    run(ts, [int(x) for x in a.rungs.split(",")], a.run_budget, a.total_budget, a.jobs, a.max_turns)
    return report()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
