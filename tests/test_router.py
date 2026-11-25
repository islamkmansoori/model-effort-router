"""Tests for scripts/router.py. Run: python3 -I tests/test_router.py   (standard library only, under a second)"""
from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
from typing import Any, Dict

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import route_agent  # noqa: E402
import router  # noqa: E402

CFG = router.load_config(pathlib.Path("/nonexistent"))
failures = 0


def check(cond: bool, label: str, info: object = None) -> None:
    global failures
    print(("PASS " if cond else "FAIL ") + label)
    if not cond and info is not None:
        print("     got:", str(info)[:600])
    failures += 0 if cond else 1


def task(size: str = "M", criteria: int = 3, extra: str = "", head: str = "") -> str:
    crit = "\n".join(f"{i}. Given a thing, then it works." for i in range(1, criteria + 1))
    return f"# A task\n\n{head}**Size** {size}\n\n**Acceptance criteria**\n{crit}\n\n{extra}\n"


def pick(text: str, **kw: object) -> tuple[str, str]:
    r = router.score_text(text, CFG, **kw)  # type: ignore[arg-type]
    return r["model"], r["effort"]


def main() -> int:
    print("== points and ladder")
    check(pick(task("S", 2)) == ("sonnet", "medium"), "small task: sonnet, medium")
    r = router.score_text(task("L", 10, "**Depends on** #1, #2, #3, #4, #5"), CFG)
    check(r["score"] == 4 + 2 + 1 and (r["model"], r["effort"]) == ("sonnet", "high"), "size L, 10 criteria, 5 deps: 7 points, sonnet high", r)
    r = router.score_text(task("XL", 10, "A new page with a migration."), CFG)
    check(r["score"] == 10 and (r["model"], r["effort"]) == ("opus", "high"), "10 points: opus high", r)
    r = router.score_text(task("S", 1, "There is no schema change and no new page."), CFG)
    check(r["score"] == 1, "a sentence that denies a signal adds nothing", r)

    print("== critical")
    ledger = task("S", 1, "It rewrites the ledger posting.")
    r = router.score_text(ledger, CFG)
    check(not r["critical"] and r["critical_guess"] and r["model"] == "sonnet"
          and any("may touch the critical part" in x for x in r["reasons"]),
          "no line: words are only named, the choice does not move", r)
    acts = dict(CFG, critical=dict(CFG["critical"], guess_acts=True))
    r = router.score_text(ledger, acts)
    check(r["critical"] and r["critical_source"] == "guessed" and (r["model"], r["effort"]) == ("opus", "xhigh"),
          "with guess_acts on, a guess is the top rung", r)
    check(not router.score_text(task("S", 1, "The ledger is not touched."), CFG)["critical_guess"], "a denial is not a guess")
    check(pick(task("S", 1, "It rewrites the ledger posting.", "**Critical:** no\n")) == ("sonnet", "medium"), "Critical: no wins over the words")
    check(pick(task("S", 1, head="**Critical:** yes\n")) == ("opus", "xhigh"), "Critical: yes is the top rung")

    print("== risk")
    r = router.score_text(task("S", 1, "It changes the pricing."), CFG)
    check(r["risk_source"] == "not set" and r["risk_words"] == ["money"] and r["model"] == "sonnet", "a guessed risk is named and moves nothing", r)
    check(pick(task("S", 1, head="**Risk:** money\n")) == ("opus", "high"), "Risk: money lifts a small task to the floor")
    check(router.score_text(task("S", 1, head="**Risk:** security, data\n"), CFG)["risk"] == ["security", "data"], "two risks are read")
    check(pick(task("S", 1, head="**Risk:** none\n")) == ("sonnet", "medium"), "Risk: none leaves the points")

    print("== retries")
    t = task("S", 1)
    check([pick(t, retry=n, cause="worker") for n in range(4)]
          == [("sonnet", "medium"), ("opus", "high"), ("opus", "xhigh"), ("opus", "xhigh")],
          "by default a worker retry jumps to the next model, then climbs, and stops at the top")
    one = dict(CFG, retry_jumps_model=False)
    check([router.score_text(t, one, retry=n, cause="worker")["effort"] for n in range(3)] == ["medium", "high", "high"]
          and router.score_text(t, one, retry=1, cause="worker")["model"] == "sonnet", "with the jump off, one rung at a time")
    check(pick(t, retry=1, cause="environment") == ("sonnet", "medium") and pick(t, retry=1, cause="task") == ("sonnet", "medium"),
          "an environment or task failure earns no step")
    check(pick(t, retry=2, cause="environment") == pick(t, retry=1, cause="worker") == ("opus", "high"),
          "a later environment failure keeps the step an earlier worker failure earned")
    tmp = pathlib.Path(tempfile.mkdtemp())
    f = tmp / "t.md"
    f.write_text(t, encoding="utf-8")
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            router.main(["score", str(f), "--retry", "1"])
        refused = False
    except SystemExit as e:
        refused = e.code == 2
    check(refused, "--retry without --cause is refused")

    print("== the choice line")
    with contextlib.redirect_stdout(io.StringIO()):
        router.main(["score", str(f), "--write"])
    once = f.read_text(encoding="utf-8")
    with contextlib.redirect_stdout(io.StringIO()):
        router.main(["score", str(f), "--write"])
    lines = f.read_text(encoding="utf-8").split("\n")
    check(once == f.read_text(encoding="utf-8") and lines[0] == "# A task" and lines[1] == "" and lines[2].startswith(router.MARK) and lines[3] == "",
          "written under the title once; writing again changes nothing", lines[:5])
    check(router.score_text(once, CFG)["score"] == router.score_text(t, CFG)["score"], "the choice line itself adds no points")
    g = tmp / "nohead.md"
    g.write_text("Just text.\n", encoding="utf-8")
    router.write_choice(g, router.score_text("x", CFG))
    check(g.read_text(encoding="utf-8").startswith(router.MARK), "a file with no title gets the line at the top")

    print("== override check")
    found = router.check_env({"CLAUDE_CODE_EFFORT_LEVEL": "low", "CLAUDE_CODE_SUBAGENT_MODEL": "haiku"})
    check(len(found) == 1 and found[0].startswith("CLAUDE_CODE_EFFORT_LEVEL=low"), "the overriding setting is named, the fallback one is not", found)

    print("== the log")
    log = tmp / "log.md"
    a, b = tmp / "a.md", tmp / "b.md"
    a.write_text(task("S", 1), encoding="utf-8")
    b.write_text(task("L", 10, head="**Risk:** none\n"), encoding="utf-8")
    router.write_choice(a, router.score_text(a.read_text(encoding="utf-8"), CFG))
    router.write_choice(b, router.score_text(b.read_text(encoding="utf-8"), CFG))
    router.record(router.load_task(str(a)), CFG, log, "sonnet/medium", "fail", 1, "environment 1", 2, 0, "", "2026-01-01")
    router.record(router.load_task(str(b)), CFG, log, "sonnet/medium", "pass", today="2026-01-01")
    router.record(router.load_task(str(a)), CFG, log, "sonnet/medium", "fail", 1, "environment 1", 2, 1, "rework", "2026-01-02")
    rows = router.read_log(log)
    check(len(rows) == 2 and rows[-1]["Task"] == "a.md" and rows[-1]["Rework"] == "1", "one row per task; recording again replaces it", rows)
    check(rows[0]["Chosen"] == "sonnet/high" and rows[0]["Risk"] == "none" and rows[1]["Risk"] == "not set", "chosen is read from the task's line", rows)
    s = "\n".join(router.summary(rows))
    check("other than chosen: 1 (b.md)" in s and "first try 0/1" in s and "too few" in s, "summary: mismatch, first tries, too few", s)

    print("== config")
    proj = tmp / "proj.json"
    proj.write_text(json.dumps({"ladder": [["haiku", "low"], ["sonnet", "medium"], ["opus", "high"], ["opus", "max"]]}), encoding="utf-8")
    cfg2 = router.load_config(proj)
    check(router.score_text(task("S", 1), cfg2)["model"] == "haiku" and cfg2["signals"] == CFG["signals"],
          "a project config replaces only the keys it names")
    ex = ROOT / "examples" / "add-discount-codes.md"
    r = router.score_text(ex.read_text(encoding="utf-8"), CFG)
    check((r["model"], r["effort"]) == ("opus", "high") and r["risk"] == ["money"], "the example task: risk money, opus high", r)

    print("== inputs: issues, labels, plain text")
    payload = {"title": "Checkout total wrong", "body": "The total ignores the fee.\n\n**Acceptance criteria**\n1. a\n2. b",
               "labels": [{"name": "size: L"}, {"name": "security"}]}

    class Resp(io.BytesIO):
        def __enter__(self) -> Resp:
            return self

        def __exit__(self, *a: object) -> None:
            return None

    seen = {}

    def opener(req: object, timeout: int = 0) -> Resp:
        seen["url"] = getattr(req, "full_url", "")
        return Resp(json.dumps(payload).encode())

    it = router.fetch_issue("https://github.com/acme/shop/issues/42", opener)
    check(seen["url"] == "https://api.github.com/repos/acme/shop/issues/42" and it.name == "acme/shop#42"
          and it.text.startswith("# Checkout total wrong"), "an issue URL is read from the GitHub API", seen)
    r = router.score_task(it, CFG)
    check("size L (+4)" in r["reasons"] and r["risk"] == ["security"] and r["risk_source"] == "labels or paths"
          and (r["model"], r["effort"]) == ("opus", "high"), "labels give the size and a risk that acts", r)
    r = router.score_task(router.Task("Fix it", "t", ["Critical"]), CFG)
    check(r["critical"] and r["critical_source"] == "label" and r["effort"] == "xhigh", "a critical label is the top rung", r)
    try:
        router.fetch_issue("not an issue")
        bad = False
    except ValueError:
        bad = True
    check(bad, "a reference that is not an issue is refused")
    check(router.score_text("Fix the bug. **Risk:** money. Then stop.", CFG)["risk"] == ["money"],
          "a **Risk:** marker inside a sentence is read")
    with contextlib.redirect_stdout(io.StringIO()) as out:
        router.main(["score", "--text", "Rename a variable", "--json"])
    check(json.loads(out.getvalue())["model"] == "sonnet", "--text scores a plain prompt")

    print("== the repository")
    files = ["src/billing/invoice.py", "src/ui/page.tsx", "src/util.py", "tests/test_util.py", "README.md",
             "a/index.ts", "b/index.ts", "c/index.ts", "d/index.ts"]
    f1 = router.repo_facts(pathlib.Path("."), "Change src/billing/invoice.py, util.py, README.md and index.ts", CFG,
                           files=files, churn={"src/util.py": 12})
    check(f1["touched"] == ["README.md", "src/billing/invoice.py", "src/util.py"],
          "named files are found; a name in many folders (index.ts) is ignored", f1)
    check(f1["untested"] == ["src/billing/invoice.py"] and f1["churned"] == ["src/util.py"],
          "untested code files and often-changed files are found; README is not code", f1)
    cfg_p = dict(CFG, risk=dict(CFG["risk"], paths={"money": ["src/billing/*"]}),
                 critical=dict(CFG["critical"], paths=["src/ui/*"]))
    r = router.score_text("Change src/billing/invoice.py", cfg_p, facts=router.repo_facts(pathlib.Path("."), "src/billing/invoice.py", cfg_p, files=files, churn={}))
    check(r["risk"] == ["money"] and r["model"] == "opus" and "1 touched file(s) without tests (+1)" in r["reasons"],
          "a configured risk path acts, and untested files add a point", r)
    r = router.score_text("Edit page.tsx", cfg_p, facts=router.repo_facts(pathlib.Path("."), "page.tsx", cfg_p, files=files, churn={}))
    check(r["critical"] and r["critical_source"] == "paths", "a configured critical path is the top rung", r)
    many = [f"m/f{i}.py" for i in range(12)]
    r = router.score_text("x", CFG, facts={"touched": many, "untested": [], "churned": []})
    check("touches 12 files (+2)" in r["reasons"], "many touched files add points", r)
    check(router.repo_facts(pathlib.Path("/nonexistent"), "a.py", CFG)["touched"] == [], "not a repository: no facts, no error")

    print("== suggestions")
    def row(sig: str, first: str, ran: str = "sonnet/medium") -> Dict[str, str]:
        return {"Signals": sig, "First try": first, "Worker retries": "0", "Review findings": "0", "Rework": "0",
                "Ran": ran, "Chosen": ran, "Task": "t", "Other failures": "none"}
    rows = [row("user interface", "fail")] * 3 + [row("database schema", "pass")] * 5 + [row("-", "fail")] * 2
    sug = router.suggestions(rows, CFG)
    check(any(s.startswith("raise 'user interface' (now 1)") for s in sug), "a signal that fails often: raise it", sug)
    check(any(s.startswith("lower 'database schema'") for s in sug), "a signal that never fails: lower it", sug)
    check(any("sonnet/medium had trouble" in s for s in sug), "a rung that fails half the time: send more up", sug)
    check(router.suggestions(rows[:4], CFG) == [], "fewer than 5 tasks: no suggestion")
    s = "\n".join(router.summary(rows, CFG))
    check("Suggested changes" in s, "summary lists the suggestions", s)

    print("== the hook")
    ev: Dict[str, Any] = {"tool_name": "Agent", "session_id": "s", "tool_input": {"subagent_type": "general-purpose", "description": "d",
          "prompt": "Small fix. **Risk:** money"}}
    ans = route_agent.decide(ev, CFG, pathlib.Path("/nonexistent"), env={})
    ui = (ans or {}).get("hookSpecificOutput", {}).get("updatedInput", {})
    check(ui.get("model") == "opus" and ui.get("effort") == "high" and ui.get("prompt") == ev["tool_input"]["prompt"]
          and ui.get("subagent_type") == "general-purpose", "the hook sets model and effort and keeps the other fields", ans)
    check("permissionDecision" not in (ans or {}).get("hookSpecificOutput", {}), "by default the hook makes no permission decision")
    kept = route_agent.decide({**ev, "tool_input": {**ev["tool_input"], "model": "haiku"}}, CFG, pathlib.Path("."), env={})
    check(kept is not None and "hookSpecificOutput" not in kept and kept["_log"]["action"] == "kept (explicit)", "an explicit model is respected")
    sk = route_agent.decide({**ev, "tool_input": {**ev["tool_input"], "subagent_type": "Explore"}}, CFG, pathlib.Path("."), env={})
    check(sk is not None and sk["_log"]["action"].startswith("skipped"), "a skipped agent type is left alone")
    check(route_agent.decide({**ev, "tool_name": "Bash"}, CFG, pathlib.Path("."), env={}) is None, "other tools are not touched")
    ov = route_agent.decide(ev, CFG, pathlib.Path("/nonexistent"), env={"CLAUDE_CODE_EFFORT_LEVEL": "low"})
    check(bool(ov and ov["_log"]["overridden_by"]), "the log says when a setting overrides the choice", ov)

    print("== the assessor")
    calls: Dict[str, Any] = {}

    class Done:
        def __init__(self, out: Dict[str, Any]) -> None:
            self.stdout = json.dumps(out)

    def fake(cmd: Any, cwd: Any) -> Done:
        calls["cmd"], calls["cwd"] = cmd, cwd
        return Done({"structured_output": {"size": "L", "files": ["src/billing/invoice.py"], "risk": ["money", "bogus"],
                                           "critical": True, "reason": "r"}, "total_cost_usd": 0.004})

    est = router.assess("Fix totals", CFG, pathlib.Path("/repo"), runner=fake)
    check(est["size"] == "L" and est["risk"] == ["money"] and est["cost_usd"] == 0.004,
          "the assessor's answer is read; an unknown risk kind is dropped", est)
    cmd = calls["cmd"]
    check("--json-schema" in cmd and "--safe-mode" in cmd and cmd[cmd.index("--model") + 1] == "haiku"
          and "Read,Glob,Grep" in cmd and calls["cwd"] == str(pathlib.Path("/repo")), "it asks a cheap model, read-only, in the repository", cmd)
    router.assess("x", CFG, None, runner=fake)
    check(calls["cmd"][calls["cmd"].index("--tools") + 1] == "" and calls["cwd"] is None, "without a repository it gets no tools")
    check(router.assess("x", CFG, None, runner=lambda c, d: Done({"structured_output": {"size": "huge"}})) == {},
          "a size outside the scale is no answer")
    check(router.assess("x", CFG, None, runner=lambda c, d: Done({})) == {}, "no structured output is no answer")

    def boom(c: Any, d: Any) -> Any:
        raise OSError("no claude")

    check(router.assess("x", CFG, None, runner=boom) == {}, "a missing claude is no answer, not an error")
    r = router.score_text("Fix totals", CFG, assessed=est)
    check("size L assessed (+4)" in r["reasons"] and not r["critical"] and r["risk"] == []
          and any("assessor suggests risk money" in x for x in r["reasons"])
          and any("assessor suggests the critical part" in x for x in r["reasons"]),
          "the assessed size acts; its risk and critical guesses are only named", r)
    r = router.score_text("**Size** S. Fix totals", CFG, assessed=est)
    check("size S (+1)" in r["reasons"], "a size written in the task wins over the assessor", r)
    fac = router.repo_facts(pathlib.Path("."), "Fix totals\nsrc/billing/invoice.py", CFG, files=files, churn={})
    check(fac["touched"] == ["src/billing/invoice.py"], "files the assessor names are looked up in the repository", fac)

    print("== climbing to the next model")
    lad = CFG["ladder"]
    check([router.climb(k, lad, True) for k in range(4)] == [2, 2, 3, 3], "jumping skips more effort on the same model")
    check([router.climb(k, lad, False) for k in range(4)] == [1, 2, 3, 3], "without the jump, one rung at a time")
    check(router.score_text(task("S", 1), CFG, retry=1, cause="worker")["model"] == "opus", "a jumping retry goes from sonnet to opus")

    print("== savings")
    proj = pathlib.Path(tempfile.mkdtemp(prefix="savings-"))
    (proj / "s1" / "subagents").mkdir(parents=True)

    def line(mid: str, model: str, u: Dict[str, Any], side: bool = False, day: str = "2026-11-20") -> str:
        return json.dumps({"timestamp": day + "T10:00:00Z", "isSidechain": side,
                           "message": {"id": mid, "model": model, "usage": u}}) + "\n"

    u1 = {"input_tokens": 1000, "output_tokens": 1000, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    u2 = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 1000000,
          "cache_creation": {"ephemeral_5m_input_tokens": 1000000, "ephemeral_1h_input_tokens": 0}}
    (proj / "s1.jsonl").write_text(line("m1", "claude-opus-5-5", u1) * 3 + line("m2", "claude-sonnet-5-5", u2)
                                   + line("m9", "claude-sonnet-5-5", u1, day="2026-10-01") + "not json\n", encoding="utf-8")
    (proj / "s1" / "subagents" / "agent-x.jsonl").write_text(line("m3", "claude-sonnet-5-5", u1, side=True)
                                                              + line("m4", "<synthetic>", u1), encoding="utf-8")
    (proj / "s2.jsonl").write_text(line("m5", "claude-unknown-9", u1) + "talks about widgets\n", encoding="utf-8")
    recs = router.read_usage([proj], since="2026-11-01")
    check(len(recs) == 4 and sum(r["subagent"] for r in recs) == 1,
          "a reply repeated on several lines counts once; older days, synthetic replies and bad lines are skipped", recs)
    check(abs(router.usage_cost(u1, [4.0, 20.0, 0.2]) - 0.024) < 1e-9, "input and output are priced per million tokens")
    check(abs(router.usage_cost(u2, [2.0, 10.0, 0.1]) - (0.1 + 2.5)) < 1e-9, "cache reads at their price, 5-minute writes at 1.25x input")
    check(router.price_of("claude-haiku-4-5-20251001", CFG["prices"]) == [1.0, 5.0, 0.1], "a dated model id matches its family's price")
    sv = router.savings(recs, CFG)
    check(sv["unpriced"] == {"claude-unknown-9": 1} and abs(sv["subagents"]["saved"] - (0.024 - 0.012)) < 1e-9
          and abs(sv["main sessions"]["at_baseline"] - (0.024 + 0.2 + 5.0)) < 1e-9,
          "savings: the same tokens priced on the baseline; unknown models are listed, not guessed", sv)
    check([r["model"] for r in router.read_usage([proj], mentions="widgets")] == ["claude-unknown-9"] and len(router.read_usage([proj], exclude=["s1"])) == 1,
          "sessions can be kept by what they mention, or left out by id")
    rep = "\n".join(router.savings_report(sv))
    check("Subagents:** $0.01 against $0.02" in rep and "claude-unknown-9" in rep, "the report states both totals", rep)

    print("== readiness (advice before starting)")
    vague = "# T\n\n**Acceptance criteria**\n1. The page works correctly.\n2. Given a valid code, then the total shows the discounted price.\n3. Loads properly\n"
    rd = router.readiness(vague, CFG)
    check(any("2 of 3 criteria are vague (correctly, properly)" in x for x in rd) and any("2 of 3 criteria are under 5 words" in x for x in rd),
          "vague and too-short criteria are named", rd)
    check(router.readiness("# T\n\n**Acceptance criteria**\n1. Given a valid code, then the total shows the discounted price.\n", CFG) == [],
          "a clear criterion raises nothing")
    check(router.readiness("word " * 130, CFG) == ["no acceptance criteria: nothing to check the result against"]
          and router.readiness("Fix the typo in the README", CFG) == [], "a long task with no criteria is flagged; a short prompt is not")
    check(any("open question" in x for x in router.readiness("Do it. Open question: which provider?", CFG))
          and router.readiness("Do it. There is no open question.", CFG) == [], "an open question is flagged, a denied one is not")
    r = router.score_text(vague, CFG)
    check(not r["ready"] and r["model"] == "sonnet" and "Before starting:" in router.choice_line(r),
          "readiness is advice: it is reported and written, and does not change the model", r)
    check(router.readiness(vague, dict(CFG, readiness=dict(CFG["readiness"], enabled=False))) == [], "readiness can be switched off")

    print("== retry limit, unknown and repeated failures")
    t0 = task("S", 1)
    r = router.score_text(t0, CFG, retry=3, cause="worker")
    check(r["stop"] and (r["model"], r["effort"]) == ("opus", "xhigh") and any("retry limit (2) reached" in x for x in r["reasons"]),
          "past the retry limit it stops climbing and says to ask a person", r)
    check(not router.score_text(t0, CFG, retry=2, cause="worker")["stop"], "at the limit it does not stop yet")
    r = router.score_text(t0, CFG, retry=2, cause="repeat")
    check((r["model"], r["effort"]) == ("opus", "high") and any("same failure on two different models" in x for x in r["reasons"]),
          "the same failure on two models earns no further step and keeps the one already earned", r)
    check(router.score_text(t0, CFG, retry=1, cause="unknown")["model"] == "sonnet", "an unknown cause earns no step")
    check(router.score_text(t0, dict(CFG, max_worker_retries=0), retry=3, cause="worker")["stop"] is False, "a limit of 0 means no limit")

    print("== whose failure was it")
    env_log = "collected 3 items\nE   ModuleNotFoundError: No module named 'requests'\n1 failed in 0.2s\n"
    sc = router.suggest_cause(env_log, CFG)
    check(sc["cause"] == "environment" and sc["counts"]["worker"] == 1 and "ModuleNotFoundError" in sc["evidence"][0],
          "environment evidence wins over a failed test", sc)
    check(router.suggest_cause("OSError: [Errno 28] No space left on device", CFG)["cause"] == "environment", "a full disk is the environment")
    check(router.suggest_cause("I stopped: the story is ambiguous about the fee.", CFG)["cause"] == "task", "an ambiguous task is the task")
    w_log = "FAILED tests/test_x.py::test_total - AssertionError: assert 90 == 100\n1 failed, 3 passed in 0.31s\n"
    check(router.suggest_cause(w_log, CFG)["cause"] == "worker", "a plain assertion failure is the worker")
    check(router.suggest_cause("all good here", CFG) == {"cause": "unknown", "counts": {"environment": 0, "task": 0, "worker": 0}, "evidence": []},
          "nothing recognised is unknown")
    fp1 = router.failure_fingerprint(w_log)
    fp2 = router.failure_fingerprint("FAILED /tmp/run-77/tests/test_x.py::test_total - AssertionError: assert 90 == 100\n1 failed, 5 passed in 2.07s\n")
    fp3 = router.failure_fingerprint("FAILED tests/test_y.py::test_other - KeyError: 'fee'\n")
    check(fp1 == fp2 and fp1 != fp3, "two runs that fail the same way share a fingerprint; a different failure does not", (fp1, fp2, fp3))
    st = pathlib.Path(tempfile.mkdtemp(prefix="state-")) / "sub" / "state.json"
    a1 = router.note_failure(st, "t.md", fp1, "sonnet")
    a2 = router.note_failure(st, "t.md", fp1, "sonnet")
    a3 = router.note_failure(st, "t.md", fp1, "opus")
    a4 = router.note_failure(st, "other.md", fp1, "opus")
    check(not a1["seen_before"] and a2["seen_before"] and a2["other_models"] == [] and a3["other_models"] == ["sonnet"]
          and not a4["seen_before"], "failures are remembered per task, with the models they were seen on", (a1, a2, a3, a4))

    work = pathlib.Path(tempfile.mkdtemp(prefix="flow-"))
    cfile, tfile, lfile = work / "cfg.json", work / "t.md", work / "fail.txt"
    cfile.write_text(json.dumps({"state": str(work / "state.json")}), encoding="utf-8")
    tfile.write_text(task("S", 1), encoding="utf-8")
    lfile.write_text(w_log, encoding="utf-8")

    def run(*args: str) -> Dict[str, Any]:
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            router.main(["--config", str(cfile), "score", str(tfile), "--json", *args])
        parsed: Dict[str, Any] = json.loads(buf.getvalue())
        return parsed

    j1 = run("--retry", "1", "--failure-log", str(lfile))
    check(j1["model"] == "opus" and any("cause suggested from the failure log: worker" in x for x in j1["reasons"]),
          "with a failure log and no --cause, a worker failure climbs", j1)
    j2 = run("--retry", "2", "--failure-log", str(lfile))
    check((j2["model"], j2["effort"]) == ("opus", "high") and any("already seen on sonnet" in x for x in j2["reasons"]),
          "the same log after the jump: it stays on opus high and says where it was seen", j2)
    lfile.write_text(env_log, encoding="utf-8")
    j3 = run("--retry", "1", "--failure-log", str(lfile))
    check(j3["model"] == "sonnet" and any("environment" in x for x in j3["reasons"]), "an environment log does not climb", j3)
    j4 = run("--retry", "1", "--failure-log", str(lfile), "--cause", "worker")
    check(j4["model"] == "opus", "an explicit --cause wins over the suggestion", j4)

    print("== mechanical tasks (off by default)")
    mech_on = dict(CFG, mechanical=dict(CFG["mechanical"], enabled=True))
    check(not router.score_text("Run the tests and report the result", CFG)["mechanical"], "off by default")
    r = router.score_text("Run the tests and report the result", mech_on)
    check(r["mechanical"] and (r["model"], r["effort"]) == ("haiku", "low"), "a task that only runs a tool goes to the cheapest rung", r)
    check(not router.score_text("Run the tests and fix the failures", mech_on)["mechanical"], "not when it asks for a change")
    check(not router.score_text("Run the tests. **Risk:** money", mech_on)["mechanical"]
          and not router.score_text("Run the tests", mech_on, retry=1, cause="worker")["mechanical"], "not when risky, and not on a retry")

    print("== budget")
    big = task("XL", 10, "A new page with a migration.")
    check(router.score_text(big, CFG)["model"] == "opus" and router.score_text(big, CFG, cap=0, cap_reason="daily budget 90% used")["model"] == "sonnet",
          "a cap lowers the first attempt of an ordinary task")
    check(router.score_text(big + "**Risk:** money", CFG, cap=0)["model"] == "opus"
          and router.score_text(big, CFG, cap=0, retry=1, cause="worker")["model"] == "opus", "risky tasks and retries are not capped")
    day = __import__("datetime").date(2026, 11, 25)
    use = {"input_tokens": 1000000, "output_tokens": 0}
    recs2 = [{"model": "claude-opus-5-5", "usage": use, "day": "2026-11-25"}, {"model": "claude-opus-5-5", "usage": use, "day": "2026-11-20"},
             {"model": "claude-opus-5-5", "usage": use, "day": "2026-11-02"}, {"model": "claude-opus-5-5", "usage": use, "day": "2026-10-30"}]
    bcfg = dict(CFG, budget=dict(CFG["budget"], daily=5, weekly=8, monthly=100))
    bs = router.budget_status([], bcfg, today=day, records=recs2)
    per = bs["periods"]
    check(per["daily"]["spent"] == 4.0 and per["daily"]["level"] == "over 75%" and per["weekly"]["spent"] == 8.0
          and per["weekly"]["level"] == "EXCEEDED" and per["monthly"]["spent"] == 12.0 and per["monthly"]["level"] == "ok" and bs["exceeded"],
          "spend per day, week and month against the limits, with alert levels", bs)
    rep_txt = "\n".join(router.budget_report(bs))
    check("Today: $4.00 of $5.00 (80%): over 75%" in rep_txt and "Last 7 days: $8.00 of $8.00 (100%): EXCEEDED" in rep_txt, "the budget report", rep_txt)
    check("no limit set" in "\n".join(router.budget_report(router.budget_status([], CFG, today=day, records=recs2))), "no limits: it says how to set them")
    bdir = pathlib.Path(tempfile.mkdtemp(prefix="budget-"))
    (bdir / ".claude").mkdir()
    now = 1795000000.0
    today_s = __import__("datetime").date.fromtimestamp(now).isoformat()
    (bdir / ".claude" / "model-router-budget.json").write_text(json.dumps({"day": today_s, "spent": 4.5, "at": now - 60}), encoding="utf-8")
    tcfg = dict(CFG, budget=dict(CFG["budget"], daily=5, throttle=True))
    cap = router.budget_cap(bdir, tcfg, now=now, folders=[bdir / "none"])
    check(cap is not None and cap["rung"] == 0 and "daily budget 90% used" in cap["reason"], "past the throttle point the hook caps ordinary tasks", cap)
    check(router.budget_cap(bdir, dict(CFG, budget=dict(CFG["budget"], daily=50, throttle=True)), now=now, folders=[bdir / "none"]) is None
          and router.budget_cap(bdir, dict(CFG, budget=dict(CFG["budget"], daily=5)), now=now) is None, "below the throttle point, or with throttle off, there is no cap")
    empty = pathlib.Path(tempfile.mkdtemp(prefix="budget2-"))
    check(router.budget_cap(empty, tcfg, now=now, folders=[empty / "none"]) is None and (empty / ".claude" / "model-router-budget.json").exists(),
          "with no cache it reads today's usage and writes the cache")
    hev: Dict[str, Any] = {"tool_name": "Agent", "session_id": "s", "tool_input": {"subagent_type": "general-purpose", "description": "d", "prompt": big}}
    orig_cap = router.budget_cap
    router.budget_cap = lambda cwd, cfg, now=None, folders=None: {"rung": 0, "reason": "daily budget 90% used: capped"}
    try:
        hans = route_agent.decide(hev, CFG, pathlib.Path("/nonexistent"), env={})
    finally:
        router.budget_cap = orig_cap
    check((hans or {})["hookSpecificOutput"]["updatedInput"]["model"] == "sonnet" and any("budget" in x for x in (hans or {})["_log"]["reasons"]),
          "the hook applies the budget cap and logs it", hans)

    print("== audit of the hook log")
    alog = pathlib.Path(tempfile.mkdtemp(prefix="audit-")) / "runs.jsonl"
    check(len(router.audit(alog)) == 1 and "No hook log" in router.audit(alog)[0], "no log: it says the hook has not routed anything here")
    alog.write_text("\n".join(json.dumps(x) for x in [
        {"time": "2026-11-25T10:00:00", "agent_type": "general-purpose", "action": "routed", "model": "sonnet", "effort": "medium", "reasons": [], "overridden_by": []},
        {"time": "2026-11-25T11:00:00", "agent_type": "general-purpose", "action": "routed", "model": "opus", "effort": "high", "reasons": ["daily budget 90% used"], "overridden_by": ["CLAUDE_CODE_EFFORT_LEVEL=low: x"]},
        {"time": "2026-11-26T09:00:00", "agent_type": "Plan", "action": "kept (explicit)", "model": "haiku", "effort": None},
        {"time": "2026-11-26T09:30:00", "agent_type": "Explore", "action": "skipped (agent type)"}]) + "\nnot json\n", encoding="utf-8")
    au = "\n".join(router.audit(alog))
    check("4 subagent starts seen by the hook, 2026-11-25 to 2026-11-26" in au and "Routed by the router: 2 (50%)" in au
          and "explicit model or effort): 1" in au and "Skipped (agent type in skip_agent_types): 1" in au
          and "sonnet/medium 1" in au and "haiku/default 1" in au and "Capped by the budget: 1" in au and "WARNING: 1 routed start(s)" in au,
          "the audit counts routed, kept, skipped, capped and overridden starts", au)

    print("== --assess auto and the faster usage read")
    check(router.stated_size("**Size** L", [], CFG) == "L" and router.stated_size("x", ["size: s"], CFG) == "S" and router.stated_size("x", [], CFG) == "?",
          "the stated size comes from the text or a label")
    calls2 = {"n": 0}
    orig_assess = router.assess

    def fake_assess(text: str, cfg: Dict[str, Any], repo: Any = None, runner: Any = None) -> Dict[str, Any]:
        calls2["n"] += 1
        return {"size": "L", "files": [], "risk": [], "critical": False, "reason": "r"}

    router.assess = fake_assess
    try:
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            router.main(["score", "--text", "**Size** S. Rename a variable", "--text", "Rework the importer", "--assess", "auto", "--json"])
    finally:
        router.assess = orig_assess
    outs = [json.loads(x) for x in buf.getvalue().splitlines()]
    check(calls2["n"] == 1 and outs[0]["size_source"] == "stated" and outs[1]["size_source"] == "assessed"
          and outs[0]["basis"] == "partly stated", "--assess auto asks only for the task that does not state its size", (calls2, outs))
    check(router.score_text("**Size** S. **Critical:** no. **Risk:** none. Do it", CFG)["basis"] == "stated"
          and router.score_text("Do it", CFG)["basis"] == "guessed", "the basis says how much of the choice rests on stated facts")
    old_dir = pathlib.Path(tempfile.mkdtemp(prefix="old-"))
    of = old_dir / "s.jsonl"
    of.write_text(json.dumps({"timestamp": "2026-11-20T10:00:00Z", "message": {"id": "o1", "model": "claude-opus-5-5", "usage": {"input_tokens": 1}}}) + "\n", encoding="utf-8")
    os.utime(of, (1700000000, 1700000000))
    check(len(router.read_usage([old_dir], since="2026-11-01")) == 0 and len(router.read_usage([old_dir])) == 1,
          "a file not touched since before the first day asked for is not read")

    print(f"\n{failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
