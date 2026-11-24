"""Tests for scripts/router.py. Run: python3 -I tests/test_router.py   (standard library only, under a second)"""
from __future__ import annotations

import contextlib
import io
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
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
    router.record(a, CFG, log, "sonnet/medium", "fail", 1, "environment 1", 2, 0, "", "2026-01-01")
    router.record(b, CFG, log, "sonnet/medium", "pass", today="2026-01-01")
    router.record(a, CFG, log, "sonnet/medium", "fail", 1, "environment 1", 2, 1, "rework", "2026-01-02")
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

    print(f"\n{failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
