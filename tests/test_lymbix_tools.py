"""Tests for lymbix_tools (the Lymbix modules as a standalone tool). Run: python3 -I tests/test_lymbix_tools.py

Standard library only. They check that the copied modules work on their own, that the one behaviour change holds
(no model call and no built-in key unless LYMBIX_LLM_URL is set), and that the benchmark's keyword baseline is
still the same scorer.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import urllib.request
from typing import Any, Dict, List

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))

import keyword_tiers  # noqa: E402
from lymbix_tools import cli  # noqa: E402
from lymbix_tools import complexity_scorer as cs  # noqa: E402
from lymbix_tools.classifier_feedback import ClassifierFeedback  # noqa: E402
from lymbix_tools.complexity_classifier import ComplexityClassifier  # noqa: E402
from lymbix_tools.config import load_config  # noqa: E402
from lymbix_tools.cost_calculator import calculate_cost, calculate_savings  # noqa: E402
from lymbix_tools.llm_guard import LLMGuard  # noqa: E402
from lymbix_tools.model_router import ModelRouter, ModelTier  # noqa: E402
from lymbix_tools.prompt_cache import PromptCache  # noqa: E402
from lymbix_tools.request_classifier import RequestClassifier, RequestType  # noqa: E402
from lymbix_tools.retry_analytics import RetryAnalytics  # noqa: E402
from lymbix_tools.routing_authority import RoutingAuthority, RoutingViolationError, lymbix_authorized  # noqa: E402
from lymbix_tools.token_logger import TokenLogger  # noqa: E402

failures = 0

SIMPLE = ("US-001: Add a delete button to the user list page.\nAs an admin, I want to delete a user so that inactive "
          "accounts are removed.\nAcceptance Criteria:\n- A delete button appears next to each user row.\n"
          "- Clicking confirms via a modal before deleting.\n")
HEAVY = ("US-112: Build the Playwright E2E test suite for the checkout flow.\nAs a QA engineer I need a full Playwright "
         "test covering the checkout pipeline including payment, inventory update, and email confirmation.\n"
         "Acceptance Criteria:\n- Test covers happy path.\n- Tests run in CI/CD pipeline (GitHub Actions).\n"
         "- Mock Stripe API for payment simulation.\n- Parallel test execution across 3 browser engines.\n"
         "- Coverage report generated per run.\n- Failed test screenshots captured automatically.\n")


def check(cond: bool, label: str, info: object = None) -> None:
    global failures
    print(("PASS " if cond else "FAIL ") + label)
    if not cond and info is not None:
        print("     got:", str(info)[:600])
    failures += 0 if cond else 1


def run_cli(*args: str) -> Dict[str, Any]:
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        cli.main([*args, "--json"])
    parsed: Dict[str, Any] = json.loads(buf.getvalue())
    return parsed


def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="lymbix-tools-"))

    print("== complexity scorer")
    scorer = cs.ComplexityScorer()
    a, b = scorer._rule_score(SIMPLE), scorer._rule_score(HEAVY)
    check(a.tier.value == "simple" and a.recommended_model == cs.TIER_MODEL_MAP[cs.ComplexityTier.SIMPLE],
          "a small interface story is simple", a.to_dict())
    check(b.tier.value == "coding_heavy" and any(s.startswith("test_heavy") for s in b.signals),
          "an end-to-end test suite story is coding-heavy, with its signals named", b.to_dict())
    check(cs.ComplexityScorer.quick_tier(SIMPLE).value == "simple", "quick_tier is the rules-only tier")
    order = cs.sort_backlog_by_complexity([{"description": HEAVY, "id": "h"}, {"description": SIMPLE, "id": "s"}])
    check([s["id"] for s in order] == ["s", "h"], "the backlog sort puts simple stories first")

    print("== the changed model call (endpoint and key from the environment only)")
    saved = {k: os.environ.pop(k, None) for k in ("LYMBIX_LLM_URL", "LITELLM_MASTER_KEY", "LYMBIX_LLM_MODEL")}
    seen: List[Any] = []
    real_open = urllib.request.urlopen

    class Reply(io.BytesIO):
        def __enter__(self) -> Reply:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def fake_open(req: Any, timeout: int = 0) -> Reply:
        seen.append(req)
        body = {"choices": [{"message": {"content": '{"tier": "complex", "confidence": 0.9, "reason": "r"}'}}]}
        return Reply(json.dumps(body).encode())

    urllib.request.urlopen = fake_open  # type: ignore[assignment]
    try:
        r0 = scorer.score(SIMPLE)
        check(not r0.llm_used and r0.tier.value == "simple" and seen == [],
              "without LYMBIX_LLM_URL no request is made and the rules decide", r0.to_dict())
        os.environ["LYMBIX_LLM_URL"] = "http://llm.invalid/v1/chat/completions"
        r1 = scorer.score(SIMPLE)
        req = seen[-1]
        headers = {k.lower(): v for k, v in req.header_items()}
        check(r1.llm_used and r1.tier.value == "complex" and req.full_url == "http://llm.invalid/v1/chat/completions"
              and "authorization" not in headers, "with an endpoint the model decides, and no key is sent unless one is set",
              (r1.to_dict(), headers))
        os.environ["LITELLM_MASTER_KEY"] = "test-key"
        os.environ["LYMBIX_LLM_MODEL"] = "my-model"
        scorer.score(SIMPLE)
        req = seen[-1]
        headers = {k.lower(): v for k, v in req.header_items()}
        check(headers.get("authorization") == "Bearer test-key" and json.loads(req.data)["model"] == "my-model",
              "the key and the model name come from the environment")
    finally:
        urllib.request.urlopen = real_open  # type: ignore[assignment]
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    src = (ROOT / "lymbix_tools" / "complexity_scorer.py").read_text(encoding="utf-8")
    check("sk-" not in src and "localhost" not in src, "the copied scorer carries no built-in key or host")

    print("== the benchmark baseline is the same scorer")
    texts = [SIMPLE, HEAVY, "Fix a typo", "Design a multi-tenant schema with role-based access control and audit logs.",
             "Optimize the search algorithm and add a redis cache with kafka events for the api endpoint."]
    same = all(keyword_tiers.tier(t)[0] == scorer._rule_score(t).tier.value
               and abs(keyword_tiers.tier(t)[1] - scorer._rule_score(t).rule_score) < 0.002 for t in texts)
    check(same, "bench/keyword_tiers.py gives the same tier and score as the Lymbix scorer")

    print("== request classifier and model router")
    rc = RequestClassifier()
    check(rc.classify("run tests with pytest") == RequestType.TOOL_TESTING and rc.is_tool_request(RequestType.TOOL_TESTING)
          and rc.get_tool_command(RequestType.TOOL_TESTING).startswith("pytest"), "a test run is a job for a local tool")
    check(rc.should_use_llm(rc.classify("why does the login fail")) and rc.classify("x", "GROOMING_1") == RequestType.META_GROOMING,
          "reasoning goes to a model; grooming stories are meta")
    pay = rc.process_payload({"request_id": "1", "raw_input": "create a webhook to sync with github"})
    check(pay["classification"]["primary_type"] == "RC-002" and "RC-010" in pay["classification"]["secondary_types"]
          and rc.process_payload({}).get("code") == 400, "the structured payload carries the taxonomy; a bad payload is refused", pay)
    check(rc.classify_failure("sqlite3.OperationalError: no such column fee") == "db_schema"
          and rc.classify_failure("assert 1 == 2 in pytest") == "test_logic" and rc.classify_failure("???") == "unknown",
          "failures are sorted into root-cause categories")
    mr = ModelRouter()
    check(mr.route("Fix typo in the readme")[0] == ModelTier.FAST and mr.route("Refactor all the deployment scripts")[0] == ModelTier.PREMIUM
          and mr.route("Implement the feature to export invoices")[0] == ModelTier.BALANCED, "the router picks fast, balanced or premium")
    check(mr.route_from_complexity(scorer._rule_score(HEAVY))[0] == ModelTier.PREMIUM
          and mr.route_from_complexity(scorer._rule_score(SIMPLE))[0] == ModelTier.FAST, "tiers follow from the complexity result")
    check(ModelRouter({"force_model_tier": "premium"}).route("Fix typo")[0] == ModelTier.PREMIUM, "a forced tier wins")
    check(ComplexityClassifier().classify("small change").value == "LIGHT"
          and ComplexityClassifier().classify("refactor the importer", ["a", "b"]).value == "HEAVY", "the quick heuristic: light and heavy")
    check(load_config(tmp) == {}, "the stand-in config is empty without a lymbix_tools.json")
    (tmp / "lymbix_tools.json").write_text('{"force_model_tier": "fast"}', encoding="utf-8")
    check(load_config(tmp) == {"force_model_tier": "fast"}, "and reads lymbix_tools.json when it exists")

    print("== guard, feedback, authority, cost")
    guard = LLMGuard(str(tmp / "retry_history"))
    check(not guard.check_loop("US-1", "same output") and guard.record_attempt("US-1", 1, "same output", 600) == 1
          and guard.check_loop("US-1", "same output") and guard.record_attempt("US-1", 1, "same output", 600) == 2,
          "a repeated output is detected as a loop")
    check(guard.check_budget("US-1", 1000) == (False, 1200) and guard.check_budget("US-1", 5000) == (True, 1200), "the story's token budget")
    fb = ClassifierFeedback(tmp / "feedback")
    for outcome in ("DONE", "FAILED", "FAILED"):
        fb.record("US-1", "RC-002", "Action Request", outcome, cost_usd=0.03)
    s = fb.summary()
    check(s["total_recorded"] == 3 and s["categories"]["RC-002"]["precision"] == 0.333 and s["low_precision_categories"] == ["RC-002"]
          and s["categories"]["RC-002"]["avg_cost_usd"] == 0.03, "feedback: precision per category, and low precision flagged", s)
    auth = RoutingAuthority()
    auth.reset()
    ok = auth.authorize("lymbix.executor", "sonnet", 100)
    try:
        auth.authorize("other.framework", "opus", 100)
        blocked = False
    except RoutingViolationError:
        blocked = True

    @lymbix_authorized(model="sonnet")
    def call() -> str:
        return "done"

    check(ok["approved"] and blocked and call() == "done" and auth.get_stats() == {"approved_calls": 2, "blocked_attempts": 1, "total_events": 3},
          "the routing authority approves its own callers, blocks others and keeps an audit log", auth.get_stats())
    check(abs(calculate_cost("gpt-4", 1000, 1000) - 0.09) < 1e-9 and calculate_savings("gpt-4", 1000, 1000) == calculate_cost("gpt-4", 1000, 1000),
          "the example cost calculator")

    print("== token logger, retry analytics, prompt cache")
    here = pathlib.Path.cwd()
    os.chdir(tmp)
    try:
        tl = TokenLogger(str(tmp / "tokens.jsonl"), budget={"daily": 1000.0, "weekly": 0, "monthly": 0})
        tl.log_request("llm_generation", 1000, 500, cache_read_tokens=200, attempt=2, story_id="US-1", model="sonnet", metadata={"model": "sonnet"})
        tl.log_request("llm_generation", 1000, 500, success=False, failure_reason="timeout", story_id="US-2")
        tl.log_tool_request("pytest", story_id="US-1")
        d = tl.get_daily_stats()
        check(d["total_requests"] == 3 and d["tool_delegated"] == 1 and d["total_retries"] == 1 and d["failure_reasons"] == {"timeout": 1}
              and d["flaky_stories"] == {"US-1": 1} and abs(d["total_cost"] - 0.02106) < 1e-6, "token log: requests, retries, tool hand-offs, cost", d)
        check(tl.get_model_stats()["by_model"]["sonnet"]["count"] == 1 and tl.check_budget_enforcement()[0], "token log: per model, budget ok")
        with contextlib.redirect_stdout(io.StringIO()):
            csv_path = tl.export_csv(str(tmp / "tokens.csv"))
        check(len(pathlib.Path(csv_path).read_text(encoding="utf-8").strip().splitlines()) == 4, "token log: CSV export")
        ra = RetryAnalytics(str(tmp / "retry_history"))
        st = ra.get_aggregate_stats()
        check(st["stories_total"] == 1 and st["total_retries"] == 1 and st["alert"] and ra.get_top_retrying_stories()[0]["story_id"] == "US-1"
              and ra.get_failure_reasons() == {"execution_failure": 2} and len(ra.check_alerts()) == 1, "retry analytics over the guard's history", st)
        pc = PromptCache(str(tmp / "cache"), ttl=3600, max_size=2)
        k1 = pc.generate_cache_key("proj", [{"path": "a.py", "hash": "1"}])
        pc.set(k1, "context one")
        got, miss = pc.get(k1), pc.get("nope")
        pc.set("k2", "two")
        pc.set("k3", "three")
        check(got == "context one" and miss is None and pc.get_stats()["size"] == 2 and pc.generate_cache_key("proj", [{"path": "a.py", "hash": "2"}]) != k1,
              "prompt cache: hit, miss, a new key when a file changes, and least-recently-used eviction", pc.get_stats())
        check(PromptCache(str(tmp / "cache"), ttl=-1).get("k3") is None, "prompt cache: expired entries are dropped")
    finally:
        os.chdir(here)

    print("== command line")
    j = run_cli("tier", SIMPLE)
    check(j["tier"] == "simple" and not j["llm_used"], "tier", j)
    check(run_cli("route", "Refactor all the deployment scripts")["tier"] == "premium" and run_cli("route", HEAVY, "--from-complexity")["tier"] == "premium", "route")
    c = run_cli("classify", "run tests with pytest")
    check(c["type"] == "tool_testing" and not c["use_llm"] and c["tool_command"].startswith("pytest"), "classify", c)
    check(run_cli("failure", "proxy error: network unreachable")["category"] == "api_bridge" and run_cli("heuristic", "small change")["tier"] == "LIGHT", "failure and heuristic")
    f1, f2 = tmp / "simple.md", tmp / "heavy.md"
    f1.write_text(SIMPLE, encoding="utf-8")
    f2.write_text(HEAVY, encoding="utf-8")
    check([r["tier"] for r in run_cli("sort", str(f2), str(f1))["order"]] == ["simple", "coding_heavy"] and run_cli("tier", str(f2))["tier"] == "coding_heavy",
          "sort, and a file path is read as the story")
    g1 = run_cli("guard", "US-9", "--output", "boom", "--exit-code", "1", "--tokens", "10", "--dir", str(tmp / "g"))
    g2 = run_cli("guard", "US-9", "--output", "boom", "--exit-code", "1", "--tokens", "10", "--dir", str(tmp / "g"), "--budget", "15")
    check(not g1["repeated_output"] and g2["repeated_output"] and g2["attempts"] == 2 and not g2["within_budget"], "guard", (g1, g2))
    run_cli("feedback", "record", "US-9", "RC-001", "Data Query", "done", "--dir", str(tmp / "fb2"))
    check(run_cli("feedback", "summary", "--dir", str(tmp / "fb2"))["overall_precision"] == 1.0, "feedback")
    os.chdir(tmp)
    try:
        tk = run_cli("tokens", "--log", str(tmp / "tokens.jsonl"))
        rt = run_cli("retries", "--dir", str(tmp / "retry_history"))
    finally:
        os.chdir(here)
    check(tk["periods"]["today"]["total_requests"] == 3 and tk["budget_ok"] and rt["stats"]["total_retries"] == 1 and rt["alerts"], "tokens and retries", (tk["periods"]["today"]["total_requests"], rt["alerts"]))

    print(f"\n{failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
