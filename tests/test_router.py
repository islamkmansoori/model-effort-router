"""Tests for scripts/router.py. Run: python3 -I tests/test_router.py   (standard library only, under a second)"""
from __future__ import annotations

import contextlib
import io
import json
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
    r = router.score_text(task("S", 1, "It rewrites the ledger posting."), CFG)
    check(r["critical"] and r["critical_source"] == "guessed" and pick(task("S", 1, "It rewrites the ledger posting.")) == ("opus", "xhigh")
          and "guessed" in "; ".join(r["reasons"]), "no line: guessed from the text, top rung, says guessed", r)
    check(not router.score_text(task("S", 1, "The ledger is not touched."), CFG)["critical"], "a denial is not a guess")
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
    check([pick(t, retry=n, cause="worker") for n in range(5)]
          == [("sonnet", "medium"), ("sonnet", "high"), ("opus", "high"), ("opus", "xhigh"), ("opus", "xhigh")], "worker retries climb and stop at the top")
    check(pick(t, retry=2, cause="environment") == ("sonnet", "medium") and pick(t, retry=2, cause="task") == ("sonnet", "medium"),
          "environment or task failures move nothing")
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
    cj = dict(CFG, retry_jumps_model=True)
    check(router.score_text(task("S", 1), cj, retry=1, cause="worker")["model"] == "opus", "a jumping retry goes from sonnet to opus")

    print(f"\n{failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
