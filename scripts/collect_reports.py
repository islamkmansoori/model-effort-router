#!/usr/bin/env python3
"""Add up the savings reports people chose to post in the repository's Discussions.

    python3 collect_reports.py                         # print the page for the home repository
    python3 collect_reports.py --out folder            # write folder/SAVINGS.md and folder/savings.json
    python3 collect_reports.py --repo owner/name       # another repository (a fork)

A report is a post that carries the block `router.py report` writes. Nothing reaches this script unless a person
published it: the router sends nothing by itself.

A post is other people's text, so this script takes numbers from it and nothing else. Every field is checked for
its type and its range, a version must look like a version and a model name like a model name, and no words from a
post are copied into the files written here. The totals are what people reported: an estimate each, not verified.
Say so wherever they are shown.

How reports add up. Each report is cumulative for one project on one machine and carries that project's random id.
The newest post per person and id counts and replaces the older ones, so posting again never counts twice. A report
whose numbers do not add up, or that is outside a plausible range, is listed and left out; deleting a post takes it
out of the total at the next run.

Standard library only. Reads https://api.github.com (public; a GITHUB_TOKEN in the environment is used if present).
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, Iterator, List, Optional, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import router  # noqa: E402

MAX_PER_DAY = 5000.0      # API-equivalent dollars a day from one project; more is left out for a person to look at
MAX_PER_TOKEN = 1e-3      # dollars per token ($1,000 a million): far above any real price
MAX_PER_PERSON = 20       # reports (projects) counted per account: the newest ones
VERSION_RE = re.compile(r"\d{1,3}\.\d{1,3}\.\d{1,3}")
MODEL_RE = re.compile(r"[a-z0-9][a-z0-9.\-]{0,59}")
ID_RE = re.compile(r"[0-9a-f]{8}")
REPO_RE = re.compile(r"[\w.-]+/[\w.-]+")
FORMAT = "written in a format this version does not read"
FIELDS = "a field is missing or is not a plain number"
SUMS = "its totals do not add up"
RANGE = "outside the plausible range"
TOO_MANY = f"more than {MAX_PER_PERSON} reports from one account"


class Bad(Exception):
    """A post that carries a report this script will not count; the text says why (one of the constants above)."""


def _count(x: Any, hi: int = 10 ** 13) -> int:
    if isinstance(x, bool) or not isinstance(x, int) or not 0 <= x <= hi:
        raise ValueError(x)
    return x


def _usd(x: Any, lo: float = 0.0, hi: float = 1e8) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float)) or x != x or not lo <= x <= hi:
        raise ValueError(x)
    return round(float(x), 2)


def _text(x: Any, pattern: "re.Pattern[str]") -> str:
    if not isinstance(x, str) or not pattern.fullmatch(x):
        raise ValueError(x)
    return x


def _model(x: Any, known: Any) -> str:
    """A model name from the shipped price list, or 'other': no name a post invents reaches the page."""
    name = _text(x, MODEL_RE)
    return name if name in known else "other"


def blocks(body: str) -> Iterator[str]:
    """The fenced blocks of a post that could hold a report."""
    for seg in (body or "")[:60000].split("```")[1::2]:
        seg = seg.strip()
        if seg.startswith("json"):
            seg = seg[4:].strip()
        if seg.startswith("{"):
            yield seg


def check_report(d: Dict[str, Any]) -> Dict[str, Any]:
    """The report with every field checked, or Bad. Only checked numbers and names come out."""
    mark = d.get(router.REPORT_KEY)
    if isinstance(mark, bool) or mark != router.REPORT_FORMAT:
        raise Bad(FORMAT)
    known = router.public_models()
    try:
        ro, models = d["routed"], d["models"]
        if not isinstance(ro, dict) or not isinstance(models, dict) or not 1 <= len(models) <= 40:
            raise ValueError("shape")
        by_name: Dict[str, Dict[str, Any]] = {}
        for n, m in models.items():
            row = {"replies": _count(m["replies"]), "tokens": _count(m["tokens"], 10 ** 15),
                   "cost_usd": _usd(m["cost_usd"]), "baseline_usd": _usd(m["baseline_usd"])}
            t = by_name.setdefault(_model(n, known), {"replies": 0, "tokens": 0, "cost_usd": 0.0, "baseline_usd": 0.0})
            for k in t:
                t[k] = round(t[k] + row[k], 2)
        rep: Dict[str, Any] = {
            "router": _text(d["router"], VERSION_RE), "id": _text(d["id"], ID_RE), "days": _count(d["days"], 3660),
            "replies": _count(d["replies"]), "tokens": _count(d["tokens"], 10 ** 15),
            "baseline": _model(d["baseline"], known), "cost_usd": _usd(d["cost_usd"]),
            "baseline_usd": _usd(d["baseline_usd"]), "saved_usd": _usd(d["saved_usd"], -1e8),
            "routed": {"subagents": _count(ro["subagents"], 10 ** 7), "replies": _count(ro["replies"]),
                       "tokens": _count(ro["tokens"], 10 ** 15), "cost_usd": _usd(ro["cost_usd"]),
                       "parent_usd": _usd(ro["parent_usd"]), "saved_usd": _usd(ro["saved_usd"], -1e8)},
            "models": dict(sorted(by_name.items()))}
    except (KeyError, TypeError, ValueError, AttributeError):
        raise Bad(FIELDS) from None

    def near(a: float, b: float) -> bool:
        return abs(a - b) <= 0.011

    ro, rows = rep["routed"], list(rep["models"].values())
    if not (near(sum(m["cost_usd"] for m in rows), rep["cost_usd"])
            and near(sum(m["baseline_usd"] for m in rows), rep["baseline_usd"])
            and near(rep["baseline_usd"] - rep["cost_usd"], rep["saved_usd"])
            and near(ro["parent_usd"] - ro["cost_usd"], ro["saved_usd"])
            and sum(m["replies"] for m in rows) == rep["replies"] and sum(m["tokens"] for m in rows) == rep["tokens"]
            and ro["replies"] <= rep["replies"] and ro["tokens"] <= rep["tokens"]
            and ro["cost_usd"] <= rep["cost_usd"] + 0.011 and ro["subagents"] <= ro["replies"]):
        raise Bad(SUMS)
    top = max(rep["cost_usd"], rep["baseline_usd"], ro["parent_usd"])
    if rep["days"] < 1 or top > MAX_PER_DAY * rep["days"] or top > rep["tokens"] * MAX_PER_TOKEN + 0.01:
        raise Bad(RANGE)
    return rep


def parse_report(body: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """(report, "") for a report that counts; (None, why) for one that does not; (None, "") for a post with no report."""
    for seg in blocks(body):
        try:
            d = json.loads(seg)
        except (ValueError, RecursionError):
            continue
        if isinstance(d, dict) and router.REPORT_KEY in d:
            try:
                return check_report(d), ""
            except Bad as e:
                return None, str(e)
    return None, ""


def add_up(posts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The totals over the newest report per person and project id."""
    newest: Dict[Tuple[str, str], Dict[str, Any]] = {}
    left_out: List[Dict[str, Any]] = []
    for p in sorted((p for p in posts if isinstance(p, dict)), key=lambda p: _number(p)):   # a later post replaces
        number = _number(p)
        rep, why = parse_report(p.get("body") if isinstance(p.get("body"), str) else "")
        if rep is None:
            if why:
                left_out.append({"post": number, "why": why})
            continue
        user = p.get("user") if isinstance(p.get("user"), dict) else {}
        newest[(str(user.get("login") or ""), rep["id"])] = {**rep, "post": number}
    per_person: Dict[str, List[Tuple[str, str]]] = {}
    for key in sorted(newest, key=lambda k: newest[k]["post"], reverse=True):
        per_person.setdefault(key[0], []).append(key)
    for who, keys in per_person.items():
        for key in keys[MAX_PER_PERSON:]:
            left_out.append({"post": newest.pop(key)["post"], "why": TOO_MANY})
    counted = sorted(newest.values(), key=lambda r: r["post"])
    models: Dict[str, Dict[str, Any]] = {}
    for r in counted:
        for name, m in r["models"].items():
            t = models.setdefault(name, {"replies": 0, "tokens": 0, "cost_usd": 0.0, "baseline_usd": 0.0})
            for k in t:
                t[k] = round(t[k] + m[k], 2)
    total: Dict[str, Any] = {"reports": len(counted), "people": len({k[0] for k in newest})}
    for k in ("days", "replies", "tokens", "cost_usd", "baseline_usd", "saved_usd"):
        total[k] = round(sum(r[k] for r in counted), 2)
    total["routed"] = {k: round(sum(r["routed"][k] for r in counted), 2)
                       for k in ("subagents", "replies", "tokens", "cost_usd", "parent_usd", "saved_usd")}
    for k in ("days", "replies", "tokens"):
        total[k] = int(total[k])
    for k in ("subagents", "replies", "tokens"):
        total["routed"][k] = int(total["routed"][k])
    return {"format": 1, "total": total, "models": dict(sorted(models.items())),
            "counted": [{k: r[k] for k in ("post", "router", "days", "baseline", "cost_usd", "baseline_usd",
                                             "saved_usd", "routed")} for r in counted],
            "left_out": sorted(left_out, key=lambda x: x["post"])}


def _number(p: Dict[str, Any]) -> int:
    n = p.get("number")
    return n if isinstance(n, int) and not isinstance(n, bool) and 0 <= n < 10 ** 9 else 0


def page(res: Dict[str, Any], repo: str) -> str:
    """SAVINGS.md: the totals, the reports behind them, and what was left out. Numbers and fixed words only."""
    def usd(x: float) -> str:
        return f"${x:,.2f}"

    def part(saved: float, against: float) -> str:
        pct = f" ({abs(saved) / against:.0%})" if against else ""
        return (f"saved {usd(saved)}" if saved >= 0 else f"cost {usd(-saved)} more") + pct

    def post(n: int) -> str:
        return f"[#{n}](https://github.com/{repo}/discussions/{n})"

    def some(n: int, one: str, many: str) -> str:
        return f"{n:,} {one if n == 1 else many}"

    t = res["total"]
    ro = t["routed"]
    out = ["# Savings people have reported", "",
           f"Added up from the reports people chose to post in [Discussions](https://github.com/{repo}/discussions) "
           "with `router.py report`. Each report is that person's own estimate, from Claude Code's usage records at API "
           "list prices; nobody has verified them. The router sends nothing by itself: a report exists only because "
           "someone published it.", ""]
    if not t["reports"]:
        out += ["**No reports yet.** To add yours, run `python3 scripts/router.py report` in a project where the "
                "router is on (or ask Claude to share what the router saved), read the post it prints, and publish it "
                "if you want to.", ""]
    else:
        out += [f"**{some(t['reports'], 'report', 'reports')} from {some(t['people'], 'person', 'people')}**, over "
                f"{some(t['days'], 'day', 'days')} of use in all: {t['replies']:,} model replies, {t['tokens']:,} tokens.", "",
                "| | Cost | Compared with | Result |", "|---|---|---|---|",
                f"| **The router's own choices** ({some(ro['subagents'], 'subagent', 'subagents')} it routed) | "
                f"{usd(ro['cost_usd'])} | "
                f"{usd(ro['parent_usd'])}, the same tokens on the model that started them | "
                f"{part(ro['saved_usd'], ro['parent_usd'])} |",
                f"| All model choice, including models picked by hand | {usd(t['cost_usd'])} | "
                f"{usd(t['baseline_usd'])}, the same tokens on each reporter's baseline model | "
                f"{part(t['saved_usd'], t['baseline_usd'])} |", "",
                "The first row is the router's own effect. The second also counts models people picked by hand, so "
                "read it as the size of the prize, not as what the router did. Both assume another model would have "
                "used the same number of tokens. The first row can be negative: the router also sends hard tasks up.", "",
                "## By model", "", "| Model | Replies | Tokens | Cost | Same tokens on the baseline |",
                "|---|---|---|---|---|"]
        out += [f"| {n} | {m['replies']:,} | {m['tokens']:,} | {usd(m['cost_usd'])} | {usd(m['baseline_usd'])} |"
                for n, m in res["models"].items()]
        out += ["", "## Reports counted", "",
                "| Post | Router | Days | The router's own choices | All model choice |", "|---|---|---|---|---|"]
        out += [f"| {post(r['post'])} | {r['router']} | {r['days']:,} | "
                f"{part(r['routed']['saved_usd'], r['routed']['parent_usd'])} | "
                f"{part(r['saved_usd'], r['baseline_usd'])} |" for r in res["counted"]]
        out.append("")
    if res["left_out"]:
        out += ["## Not counted", ""] + [f"- {post(x['post'])}: {x['why']}" for x in res["left_out"]] + [""]
    out += ["How this is put together: `scripts/collect_reports.py` on the main branch. The newest report per person "
            "and project counts, so posting again never counts twice."]
    return "\n".join(out) + "\n"


def _get(url: str, token: str, opener: Any) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "model-effort-router"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with (opener or urllib.request.urlopen)(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_posts(repo: str, token: str = "", opener: Any = None, pages: int = 50) -> List[Dict[str, Any]]:
    """Every discussion of a public repository, from GitHub's API. A token that is refused is dropped: the posts
    are public, so they are then read as anyone can read them."""
    out: List[Dict[str, Any]] = []
    for n in range(1, pages + 1):
        url = f"https://api.github.com/repos/{repo}/discussions?per_page=100&page={n}"
        try:
            batch = _get(url, token, opener)
        except urllib.error.HTTPError as e:
            if not (token and e.code in (401, 403, 404)):
                raise
            token = ""
            batch = _get(url, token, opener)
        if not isinstance(batch, list) or not batch:
            break
        out += batch
        if len(batch) < 100:
            break
    return out


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(prog="collect_reports.py", description="Add up the savings reports posted in Discussions.")
    ap.add_argument("--repo", default=router.HOME_REPO, help="owner/name (default: the home repository)")
    ap.add_argument("--out", type=pathlib.Path, help="folder to write SAVINGS.md and savings.json into")
    ap.add_argument("--posts", type=pathlib.Path, help="read the posts from a JSON file instead of GitHub")
    a = ap.parse_args(argv)
    if not REPO_RE.fullmatch(a.repo):
        print(f"--repo must be owner/name, got {a.repo}")
        return 2
    if a.posts:
        posts = json.loads(a.posts.read_text(encoding="utf-8"))
    else:
        posts = fetch_posts(a.repo, os.environ.get("GITHUB_TOKEN", ""))
    res = add_up(posts if isinstance(posts, list) else [])
    text = page(res, a.repo)
    if a.out:
        a.out.mkdir(parents=True, exist_ok=True)
        (a.out / "SAVINGS.md").write_text(text, encoding="utf-8")
        (a.out / "savings.json").write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
        t = res["total"]
        print(f"{len(posts)} posts read, {t['reports']} reports counted, {len(res['left_out'])} left out: {a.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
