#!/usr/bin/env python3
"""Command line for the routing tools (written for model-effort-router; the modules it calls are from an earlier project by the same author).

    python3 routing_tools/cli.py tier STORY.md            # complexity tier and the model would pick
    python3 routing_tools/cli.py tier STORY.md --llm      # ask a model first (needs ROUTING_LLM_URL), rules as fallback
    python3 routing_tools/cli.py route STORY.md           # model tier: fast, balanced or premium, with the reason
    python3 routing_tools/cli.py classify "run tests with pytest"   # a model request, or a local tool (which command)?
    python3 routing_tools/cli.py failure "db schema error: no such column"   # root-cause category of a failure
    python3 routing_tools/cli.py sort A.md B.md C.md      # simple stories first, coding-heavy last
    python3 routing_tools/cli.py guard US-42 --output out.txt --exit-code 1 --tokens 1800   # loop and budget guard
    python3 routing_tools/cli.py feedback record US-42 RC-002 "Action Request" DONE --model sonnet --cost 0.02
    python3 routing_tools/cli.py feedback summary
    python3 routing_tools/cli.py tokens                   # token and cost totals from .routing_data/token_usage.jsonl
    python3 routing_tools/cli.py retries                  # retry statistics from .routing_data/retry_history

Text arguments may be a file path, a quoted string, or - for standard input. Add --json to any command for JSON.
Standard library only; `rich` is optional and only used by the modules' own dashboards.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any, Dict, List

if __package__ in (None, ""):          # run as a script: make the package importable
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from routing_tools.classifier_feedback import ClassifierFeedback  # noqa: E402
from routing_tools.complexity_classifier import ComplexityClassifier  # noqa: E402
from routing_tools.complexity_scorer import ComplexityScorer, sort_backlog_by_complexity  # noqa: E402
from routing_tools.llm_guard import LLMGuard  # noqa: E402
from routing_tools.model_router import ModelRouter  # noqa: E402
from routing_tools.request_classifier import FAILURE_TAXONOMY, RequestClassifier  # noqa: E402
from routing_tools.retry_analytics import RetryAnalytics  # noqa: E402
from routing_tools.token_logger import TokenLogger  # noqa: E402


def text_of(arg: str) -> str:
    """A file's content, standard input for "-", or the argument itself."""
    if arg == "-":
        return sys.stdin.read()
    try:
        p = pathlib.Path(arg)
        if len(arg) < 1024 and p.is_file():
            return p.read_text(encoding="utf-8", errors="ignore")
    except (OSError, ValueError):
        pass
    return arg


def emit(data: Dict[str, Any], lines: List[str], as_json: bool) -> None:
    """Print JSON, or plain lines that survive a terminal that cannot show every character."""
    text = json.dumps(data, indent=1, default=str) if as_json else "\n".join(lines)
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, errors="replace").decode(enc, errors="replace"))


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(prog="routing_tools", description="Routing and token tools, standalone.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--json", action="store_true")
        return p

    p = add("tier", "complexity tier (simple, mid, complex, coding_heavy) and the recommended model")
    p.add_argument("text")
    p.add_argument("--llm", action="store_true", help="ask a model first (ROUTING_LLM_URL), fall back to the rules")
    p = add("route", "model tier (fast, balanced, premium) with the reason")
    p.add_argument("text")
    p.add_argument("--story-id", default="")
    p.add_argument("--from-complexity", action="store_true", help="derive the tier from the complexity scorer")
    p = add("heuristic", "light, medium or heavy from length, file count and a few keywords")
    p.add_argument("text")
    p.add_argument("--files", type=int, default=0, help="number of context files")
    p = add("classify", "is this a model request or a job for a local tool?")
    p.add_argument("text")
    p.add_argument("--story-id", default="")
    p = add("failure", "root-cause category of a failure message")
    p.add_argument("text")
    p = add("sort", "order stories: simple first, coding-heavy last")
    p.add_argument("files", nargs="+")
    p = add("guard", "record an attempt; detect a repeated output and an exhausted token budget")
    p.add_argument("story_id")
    p.add_argument("--output", required=True, help="the attempt's output (file, text or -)")
    p.add_argument("--exit-code", type=int, default=0)
    p.add_argument("--tokens", type=int, default=0)
    p.add_argument("--budget", type=int, default=100000, help="token budget for the story")
    p.add_argument("--dir", default=".routing_data/retry_history")
    p = add("feedback", "record a classification's outcome, or summarise precision per category")
    p.add_argument("action", choices=("record", "summary"))
    p.add_argument("fields", nargs="*", help="record: story_id classified_as category_label outcome")
    p.add_argument("--model", default="unknown")
    p.add_argument("--duration", type=float, default=0.0)
    p.add_argument("--tokens", type=int, default=0)
    p.add_argument("--cost", type=float, default=0.0)
    p.add_argument("--dir", default=".routing_data/classifier_feedback")
    p = add("tokens", "token and cost totals from the token log")
    p.add_argument("--log", default=".routing_data/token_usage.jsonl")
    p.add_argument("--export", help="write the log as CSV to this path")
    p = add("retries", "retry statistics and alerts")
    p.add_argument("--days", type=int, default=None, help="only attempts from the last N days (default: all; "
                   "attempts recorded by `guard` carry no date and count only without --days)")
    p.add_argument("--dir", default=".routing_data/retry_history")
    a = ap.parse_args(argv)

    if a.cmd == "tier":
        scorer = ComplexityScorer()
        res = scorer.score(text_of(a.text)) if a.llm else scorer._rule_score(text_of(a.text))
        d = res.to_dict()
        emit(d, [f"tier: {d['tier']}  (confidence {d['confidence']:.0%}, rule score {d['rule_score']})",
                 f"recommended model: {d['recommended_model']}",
                 f"signals: {', '.join(d['signals']) or 'none'}"]
             + ([f"model's reason: {d['llm_reason']}"] if d["llm_used"] else []), a.json)
        return 0
    if a.cmd == "route":
        router = ModelRouter()
        text = text_of(a.text)
        if a.from_complexity:
            tier, reason = router.route_from_complexity(ComplexityScorer()._rule_score(text))
        else:
            tier, reason = router.route(text, a.story_id)
        emit({"tier": tier.value, "reason": reason}, [f"{tier.value}: {reason}"], a.json)
        return 0
    if a.cmd == "heuristic":
        t = ComplexityClassifier().classify(text_of(a.text), ["f"] * a.files)
        emit({"tier": t.value}, [t.value], a.json)
        return 0
    if a.cmd == "classify":
        rc = RequestClassifier()
        text = text_of(a.text)
        kind = rc.classify(text, a.story_id)
        d = {"type": kind.value, "use_llm": rc.should_use_llm(kind), "tool_command": rc.get_tool_command(kind),
             "payload": rc.process_payload({"request_id": a.story_id or "cli", "raw_input": text})}
        lines = [f"type: {kind.value}", "handled by: " + ("a model" if d["use_llm"] else "a local tool or the pipeline")]
        if d["tool_command"]:
            lines.append(f"tool command: {d['tool_command']}")
        c = d["payload"].get("classification", {})
        if c:
            lines.append(f"taxonomy: {c.get('primary_type')} {c.get('primary_type_label')} "
                         f"(priority {c.get('priority')}, confidence {c.get('confidence_score')})")
        emit(d, lines, a.json)
        return 0
    if a.cmd == "failure":
        cat = RequestClassifier().classify_failure(text_of(a.text))
        emit({"category": cat, "label": FAILURE_TAXONOMY[cat]}, [f"{cat}: {FAILURE_TAXONOMY[cat]}"], a.json)
        return 0
    if a.cmd == "sort":
        stories = [{"file": f, "description": text_of(f)} for f in a.files]
        ordered = sort_backlog_by_complexity(stories)
        rows = [{"file": s["file"], "tier": ComplexityScorer.quick_tier(s["description"]).value} for s in ordered]
        emit({"order": rows}, [f"{r['tier']:<13} {r['file']}" for r in rows], a.json)
        return 0
    if a.cmd == "guard":
        guard = LLMGuard(a.dir)
        output = text_of(a.output)
        loop = guard.check_loop(a.story_id, output)
        attempts = guard.record_attempt(a.story_id, a.exit_code, output, a.tokens)
        within, used = guard.check_budget(a.story_id, a.budget)
        d = {"story": a.story_id, "attempts": attempts, "repeated_output": loop, "tokens_used": used,
             "within_budget": within}
        emit(d, [f"{a.story_id}: attempt {attempts}, {used:,} of {a.budget:,} tokens used",
                 "repeated output: " + ("YES, the same output as an earlier attempt (a loop)" if loop else "no"),
                 "budget: " + ("ok" if within else "EXHAUSTED, stop retrying")], a.json)
        return 1 if loop or not within else 0
    if a.cmd == "feedback":
        fb = ClassifierFeedback(a.dir)
        if a.action == "record":
            if len(a.fields) != 4:
                ap.error("feedback record needs: story_id classified_as category_label outcome")
            fb.record(a.fields[0], a.fields[1], a.fields[2], a.fields[3].upper(), a.model, a.duration, a.tokens, a.cost)
            emit({"recorded": a.fields[0]}, [f"recorded {a.fields[0]}: {a.fields[1]} -> {a.fields[3].upper()}"], a.json)
            return 0
        s = fb.summary()
        lines = [f"{s['total_recorded']} outcomes recorded, overall precision {s['overall_precision']:.0%}"]
        for key, v in s.get("categories", {}).items():
            lines.append(f"  {key} {v['category_label']}: {v['done']}/{v['total']} done "
                         f"({v['precision']:.0%}), average cost ${v['avg_cost_usd']:.4f}")
        if s.get("low_precision_categories"):
            lines.append("low precision (under 60% over 3 or more): " + ", ".join(s["low_precision_categories"]))
        emit(s, lines, a.json)
        return 0
    if a.cmd == "tokens":
        tl = TokenLogger(a.log)
        if a.export:
            path = tl.export_csv(a.export)
            emit({"exported": path}, [f"exported to {path}"], a.json)
            return 0
        periods = {"today": tl.get_daily_stats(), "last_7_days": tl.get_weekly_stats(),
                   "last_30_days": tl.get_monthly_stats()}
        allowed, why = tl.check_budget_enforcement()
        d = {"periods": periods, "by_model": tl.get_model_stats(), "budget_ok": allowed, "budget_note": why}
        lines = [f"{name}: {s['total_requests']} requests, {s['total_tokens']:,} tokens, ${s['total_cost']:.4f}, "
                 f"cache hits {s['cache_hit_rate']:.0%}, retries per request {s['avg_retries']:.2f}, "
                 f"handled by tools {s['tool_delegated']}" for name, s in periods.items()]
        lines.append(f"budget: {why}")
        emit(d, lines, a.json)
        return 0 if allowed else 1
    if a.cmd == "retries":
        ra = RetryAnalytics(a.dir)
        s = ra.get_aggregate_stats(since_days=a.days)
        alerts = ra.check_alerts()
        d = {"stats": s, "top": ra.get_top_retrying_stories(), "failure_reasons": ra.get_failure_reasons(),
             "alerts": alerts}
        period = f"last {a.days} days" if a.days else "all time"
        lines = [f"{period}: {s['stories_total']} stories, {s['total_attempts']} attempts, "
                 f"{s['total_retries']} retries ({s['retry_rate']:.0%}), "
                 f"{s['tokens_wasted_on_failures']:,} tokens spent on failed attempts"]
        lines += [f"  {t['story_id']}: {t['retries']} retries, {t['failures']} failures" for t in d["top"]]
        lines += alerts or ["no retry alerts"]
        emit(d, lines, a.json)
        return 1 if alerts else 0
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
