# Copied from an earlier project by the same author (commit d305040, 2026-10-09). Apache-2.0, see LICENSE.
# Modified for model-effort-router on 2026-11-25: the model call in _call_llm_first() reads its endpoint, key and
# model from the environment (ROUTING_LLM_URL, LITELLM_MASTER_KEY, ROUTING_LLM_MODEL) and is skipped when no
# endpoint is set; the original called a local proxy with a built-in default key. Nothing else is changed.
"""
Complexity Scorer
======================
Plugs into: Executor (before model selection)
Strategy  : Rule-based fast pass → free LLM call if confidence is low
Output    : ComplexityResult with tier, model recommendation, confidence

Integration point in your Executor:
    scorer  = ComplexityScorer()
    result  = scorer.score(story_text)
    model   = result.recommended_model   # pass this to MultiModelEngine
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

# ---------------------------------------------------------------------------
# Enums & Data Classes
# ---------------------------------------------------------------------------


class ComplexityTier(str, Enum):
    """
    Four tiers that map to the model chain.

    SIMPLE       → free models  (Groq Llama, Gemini Flash)
    MID          → cheap paid   (Mistral, Together AI)
    COMPLEX      → mid paid     (Gemini Pro, Claude Haiku)
    CODING_HEAVY → premium      (Claude Sonnet via Antigravity bridge)
    """

    SIMPLE = "simple"
    MID = "mid"
    COMPLEX = "complex"
    CODING_HEAVY = "coding_heavy"


# Map tier → preferred model string (match your existing model IDs)
TIER_MODEL_MAP: dict[ComplexityTier, str] = {
    ComplexityTier.SIMPLE: "groq/llama-3.3-70b-versatile",
    ComplexityTier.MID: "mistral/mistral-medium-latest",
    ComplexityTier.COMPLEX: "gemini/gemini-2.0-pro",
    ComplexityTier.CODING_HEAVY: "antigravity",  # routes via bridge
}

# Confidence below this threshold → trigger free LLM call for accuracy
CONFIDENCE_THRESHOLD = 0.72


@dataclass
class ComplexityResult:
    tier: ComplexityTier
    confidence: float  # 0.0 – 1.0
    recommended_model: str
    rule_score: float  # raw rule engine score
    llm_used: bool = False  # did we call the free LLM?
    llm_reason: Optional[str] = None  # explanation from LLM
    signals: list[str] = field(default_factory=list)  # matched rules

    def to_dict(self) -> dict:
        return {
            "tier": self.tier.value,
            "confidence": round(self.confidence, 3),
            "recommended_model": self.recommended_model,
            "rule_score": round(self.rule_score, 3),
            "llm_used": self.llm_used,
            "llm_reason": self.llm_reason,
            "signals": self.signals,
        }


# ---------------------------------------------------------------------------
# Rule Engine
# ---------------------------------------------------------------------------


@dataclass
class _Rule:
    name: str
    pattern: re.Pattern | None  # None = uses a custom callable
    score: float  # positive = increases complexity
    tier_hint: ComplexityTier | None = None


# Each rule contributes a score toward the final complexity bucket.
# Tune these weights based on your domain experience.
_RULES: list[_Rule] = [
    # ── SIMPLE signals (negative score → pulls toward SIMPLE) ──────────────
    _Rule(
        "crud_keywords",
        re.compile(
            r"\b(crud|create|read|update|delete|list|fetch|get|put|"
            r"post|form|field|table|basic|simple|display|show)\b",
            re.I,
        ),
        score=-0.12,
    ),
    _Rule(
        "ui_only",
        re.compile(
            r"\b(button|modal|dropdown|navbar|sidebar|icon|tooltip|"
            r"label|layout|color|style|css|theme|responsive)\b",
            re.I,
        ),
        score=-0.10,
    ),
    _Rule(
        "short_story",  # fewer than 80 words = probably simple
        pattern=None,
        score=-0.15,
    ),
    _Rule(
        "few_acceptance_criteria",  # ≤ 2 ACs = simple
        pattern=None,
        score=-0.12,
    ),
    # ── MID signals ─────────────────────────────────────────────────────────
    _Rule(
        "api_integration",
        re.compile(
            r"\b(api|endpoint|rest|graphql|webhook|http|request|"
            r"response|payload|header|auth(entication)?|jwt|oauth)\b",
            re.I,
        ),
        score=0.10,
    ),
    _Rule(
        "business_logic",
        re.compile(
            r"\b(rule|workflow|validat|calculat|transform|process|"
            r"pipeline|condition|branch|decision|trigger|event)\b",
            re.I,
        ),
        score=0.12,
    ),
    _Rule(
        "moderate_acceptance_criteria",  # 3-5 ACs
        pattern=None,
        score=0.15,
    ),
    # ── COMPLEX signals ─────────────────────────────────────────────────────
    _Rule(
        "architecture",
        re.compile(
            r"\b(architect|schema|design|model|relation|normaliz|"
            r"microservice|service|component|module|layer|abstraction|"
            r"pattern|dependency|inject)\b",
            re.I,
        ),
        score=0.25,
    ),
    _Rule(
        "multi_system",
        re.compile(
            r"\b(integrat|sync|async|queue|kafka|rabbitmq|redis|"
            r"cache|message|broker|event.driven|distributed|"
            r"multi.tenant|scalab)\b",
            re.I,
        ),
        score=0.28,
    ),
    _Rule(
        "security",
        re.compile(
            r"\b(secur|encrypt|decrypt|hash|permission|role|rbac|" r"acl|audit|compliance|gdpr|pii|vulnerab)\b", re.I
        ),
        score=0.22,
    ),
    _Rule(
        "many_acceptance_criteria",  # 6+ ACs
        pattern=None,
        score=0.20,
    ),
    _Rule(
        "agile_generation",
        re.compile(r"\b(TASK: GENERATE USER STORY|CONVERT BUSINESS REQUIREMENT TO BACKLOG|REFINE USER STORY)\b", re.I),
        score=4.0,
    ),
    # ── CODING_HEAVY signals ─────────────────────────────────────────────────
    _Rule(
        "algorithm_heavy",
        re.compile(
            r"\b(algorithm|optimize|performance|O\(|complexity|"
            r"sort|search|graph|tree|dynamic.program|recursive|"
            r"concurrent|parallel|thread|async await|coroutine)\b",
            re.I,
        ),
        score=0.35,
    ),
    _Rule(
        "deep_coding",
        re.compile(
            r"\b(write.*function|build.*class|build.*module|build.*sdk|"
            r"write.*parser|write.*compiler|write.*library|"
            r"implement.*algorithm|build.*cli|serialize|deserializ|"
            r"write.*middleware|build.*engine)\b",
            re.I,
        ),
        score=0.30,
    ),
    _Rule(
        "infra_or_devops",
        re.compile(
            r"\b(docker|kubernetes|ci.cd|pipeline|terraform|"
            r"deploy|infra|cloud|aws|gcp|azure|serverless|"
            r"container|helm|nginx|load.balanc)\b",
            re.I,
        ),
        score=0.32,
    ),
    _Rule(
        "architectural_design",
        re.compile(
            r"\b(architecture|pattern|framework|design.system|refactor|"
            r"decouple|interface|abstract|orchestrat|governance)\b",
            re.I,
        ),
        score=0.38,
    ),
    _Rule(
        "premium_hint",
        re.compile(r"\b(premium|thinking|high.reasoning|sota|gpt-4|claude-3|gemini-3)\b", re.I),
        score=0.45,
    ),
    _Rule(
        "test_heavy",
        re.compile(
            r"\b(test|playwright|cypress|jest|pytest|unit.test|" r"integration.test|e2e|coverage|mock|fixture|stub)\b",
            re.I,
        ),
        score=0.20,
    ),
]

# Score → tier thresholds  (tweak if routing feels off)
_THRESHOLDS = [
    (0.40, ComplexityTier.CODING_HEAVY),
    (0.20, ComplexityTier.COMPLEX),
    (0.05, ComplexityTier.MID),
    (-9.99, ComplexityTier.SIMPLE),
]


def _count_acceptance_criteria(text: str) -> int:
    """Count lines that look like acceptance criteria."""
    ac_pattern = re.compile(
        r"^\s*[-*•]\s+\S",  # any bullet line with content
        re.MULTILINE,
    )
    explicit = re.compile(r"\b(given|when|then|ac\s*\d+|acceptance criteria)\b", re.I)
    bullet_lines = len(ac_pattern.findall(text))
    explicit_hits = len(explicit.findall(text))
    return max(bullet_lines, explicit_hits)


def _word_count(text: str) -> int:
    return len(text.split())


# ---------------------------------------------------------------------------
# Free LLM Classifier
# ---------------------------------------------------------------------------

_LLM_SYSTEM_PROMPT = """You are an expert software engineering task classifier.
Your job is to classify a user story or task into one of four complexity tiers:

  simple       – UI-only, CRUD, basic form, display data, no logic
  mid          – API integration, business rules, validation, moderate logic
  complex      – multi-system, architecture design, security, 6+ acceptance criteria
  coding_heavy – algorithm, deep implementation, DevOps, full module build, E2E tests

Return ONLY valid JSON in exactly this format (no markdown, no explanation):
{
  "tier": "simple|mid|complex|coding_heavy",
  "confidence": <float 0.0-1.0>,
  "reason": "<one concise sentence explaining your choice>"
}"""


def _call_llm_first(story_text: str) -> Optional[dict]:
    """
    LLM-First Fast Pass. Hits the model proxy bridge to semantically classify the story.
    Returns {"tier": "...", "confidence": 0.99, "reason": "..."}
    """
    import urllib.request
    import urllib.error

    # Changed for model-effort-router: the endpoint and key come only from the environment (no default key is
    # shipped), and without ROUTING_LLM_URL there is no model call, so score() falls back to the rules.
    llm_url = os.environ.get("ROUTING_LLM_URL", "")
    if not llm_url:
        return None
    api_key = os.environ.get("LITELLM_MASTER_KEY", "")

    payload = json.dumps(
        {
            "model": os.environ.get("ROUTING_LLM_MODEL", "gemini-flash"),
            "temperature": 0.1,
            "max_tokens": 200,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an expert software architect analyzing task complexity. "
                        "Output a JSON object with strictly these keys: "
                        "'tier' (one of: 'simple', 'mid', 'complex', 'coding_heavy'), "
                        "'confidence' (float 0.0 to 1.0), "
                        "'reason' (a 1-sentence explanation). "
                        "Ignore task length. Focus purely on semantic and architectural complexity."
                    ),
                },
                {"role": "user", "content": f"Classify this story:\n\n{story_text[:3000]}"},
            ],
        }
    ).encode()

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(llm_url, data=payload, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
            content = body["choices"][0]["message"]["content"].strip()
            content = re.sub(r"^```json|```$", "", content, flags=re.MULTILINE).strip()
            return json.loads(content)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Main Scorer Class
# ---------------------------------------------------------------------------


class ComplexityScorer:
    """
    Usage inside your Executor:

        scorer = ComplexityScorer()          # create once, reuse
        result = scorer.score(story_text)
        model  = result.recommended_model    # "groq/...", "mistral/...", or "antigravity"

    Optional — pre-score at backlog level for prioritization:
        tier = ComplexityScorer.quick_tier(story_text)
    """

    def __init__(self, confidence_threshold: float = CONFIDENCE_THRESHOLD):
        self.confidence_threshold = confidence_threshold
        self._rules = _RULES

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score(self, story_text: str) -> ComplexityResult:
        """Full score: LLM-first semantic pass, fallback to rules if LLM fails."""
        llm_data = _call_llm_first(story_text)
        
        if llm_data:
            try:
                tier = ComplexityTier(llm_data["tier"].lower())
                return ComplexityResult(
                    tier=tier,
                    confidence=float(llm_data.get("confidence", 0.9)),
                    recommended_model=TIER_MODEL_MAP[tier],
                    rule_score=0.0,
                    llm_used=True,
                    llm_reason=llm_data.get("reason", "Semantic classification"),
                    signals=["llm_first_pass"]
                )
            except (ValueError, KeyError):
                pass
                
        # Fallback to rules if LLM fails
        return self._rule_score(story_text)

    @staticmethod
    def quick_tier(story_text: str) -> ComplexityTier:
        """
        Zero-cost instant tier for backlog ordering/prioritization.
        No LLM call — rules only.
        """
        scorer = ComplexityScorer()
        return scorer._rule_score(story_text).tier

    # ------------------------------------------------------------------
    def _rule_score(self, text: str) -> ComplexityResult:
        text = text or ""
        signals: list[str] = []
        total_score = 0.0
        wc = _word_count(text)
        ac_count = _count_acceptance_criteria(text)

        for rule in self._rules:
            # Dynamic rules (no pattern)
            if rule.name == "short_story":
                if wc < 80:
                    total_score += rule.score
                    signals.append(f"short_story (words={wc})")
                continue

            if rule.name == "few_acceptance_criteria":
                if ac_count <= 2:
                    total_score += rule.score
                    signals.append(f"few_ac (count={ac_count})")
                continue

            if rule.name == "moderate_acceptance_criteria":
                if 3 <= ac_count <= 5:
                    total_score += rule.score
                    signals.append(f"moderate_ac (count={ac_count})")
                continue

            if rule.name == "many_acceptance_criteria":
                if ac_count >= 6:
                    total_score += rule.score
                    signals.append(f"many_ac (count={ac_count})")
                continue

            # Pattern-based rules — count matches, cap contribution
            if rule.pattern:
                matches = len(rule.pattern.findall(text))
                if matches > 0:
                    contribution = rule.score * min(matches, 2)  # cap at 2x
                    total_score += contribution
                    signals.append(f"{rule.name} (hits={matches})")

        # Map score to tier
        tier = ComplexityTier.SIMPLE
        for threshold, t in _THRESHOLDS:
            if total_score >= threshold:
                tier = t
                break

        confidence = self._score_to_confidence(total_score, tier)

        return ComplexityResult(
            tier=tier,
            confidence=confidence,
            recommended_model=TIER_MODEL_MAP[tier],
            rule_score=total_score,
            signals=signals,
        )

    @staticmethod
    def _score_to_confidence(score: float, tier: ComplexityTier) -> float:
        """
        Higher confidence when the score is well inside a tier's range,
        lower when it's near a boundary.
        """
        if tier == ComplexityTier.CODING_HEAVY:
            distance = score - 0.40
        elif tier == ComplexityTier.COMPLEX:
            distance = min(score - 0.20, 0.40 - score)
        elif tier == ComplexityTier.MID:
            distance = min(score - 0.05, 0.20 - score)
        else:  # SIMPLE
            distance = abs(score - 0.05)

        confidence = 0.55 + min(distance * 4.0, 0.45)
        return round(max(0.50, min(confidence, 0.99)), 3)

    @staticmethod
    def _merge(rule_result: ComplexityResult, llm_data: dict) -> ComplexityResult:
        """Blend rule result with LLM output. LLM wins on tier when more confident."""
        try:
            llm_tier = ComplexityTier(llm_data["tier"])
            llm_confidence = float(llm_data.get("confidence", 0.5))
            llm_reason = llm_data.get("reason", "")
        except (KeyError, ValueError):
            return rule_result  # bad LLM response — fall back to rules

        if llm_confidence > rule_result.confidence:
            final_tier = llm_tier
            final_confidence = llm_confidence
        else:
            final_tier = rule_result.tier
            if rule_result.tier == llm_tier:
                final_confidence = min(rule_result.confidence + 0.10, 0.99)
            else:
                final_confidence = rule_result.confidence

        return ComplexityResult(
            tier=final_tier,
            confidence=final_confidence,
            recommended_model=TIER_MODEL_MAP[final_tier],
            rule_score=rule_result.rule_score,
            llm_used=True,
            llm_reason=llm_reason,
            signals=rule_result.signals,
        )


# ---------------------------------------------------------------------------
# Executor Integration Helper
# ---------------------------------------------------------------------------


def get_model_for_story(story_text: str, scorer: Optional[ComplexityScorer] = None) -> tuple[str, ComplexityResult]:
    """
    Drop-in helper for your Executor.

    Returns (model_string, full_result) — use model_string directly
    as the model param in MultiModelEngine or to trigger AntigravityBridge.

    Example in executor.py:
        model, score_result = get_model_for_story(story.full_text)
        token_logger.log(score_result.to_dict())

        if model == "antigravity":
            AntigravityBridge().delegate(story)
        else:
            MultiModelEngine(preferred_model=model).run(story)
    """
    if scorer is None:
        scorer = ComplexityScorer()
    result = scorer.score(story_text)
    return result.recommended_model, result


# ---------------------------------------------------------------------------
# Backlog Pre-Scorer (for BacklogEngine ordering)
# ---------------------------------------------------------------------------


def sort_backlog_by_complexity(stories: list[dict], text_key: str = "description") -> list[dict]:
    """
    Optional: sort backlog stories so SIMPLE tasks run first (warm up),
    CODING_HEAVY tasks run last (save premium model for end).

    stories: list of dicts with at least {text_key: "story text..."}
    Returns same list re-ordered.
    """
    tier_order = {
        ComplexityTier.SIMPLE: 0,
        ComplexityTier.MID: 1,
        ComplexityTier.COMPLEX: 2,
        ComplexityTier.CODING_HEAVY: 3,
    }
    scored = []
    for story in stories:
        tier = ComplexityScorer.quick_tier(story.get(text_key, ""))
        scored.append((tier_order[tier], story))
    scored.sort(key=lambda x: x[0])
    return [s for _, s in scored]


# ---------------------------------------------------------------------------
# CLI / Debug Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from textwrap import dedent

    _DEMOS = {
        "simple": dedent("""
            US-001: Add a delete button to the user list page.
            As an admin, I want to delete a user so that inactive accounts are removed.
            Acceptance Criteria:
            - A delete button appears next to each user row.
            - Clicking confirms via a modal before deleting.
        """),
        "mid": dedent("""
            US-042: Implement JWT authentication for the REST API.
            As a developer, I want all API endpoints to require a valid JWT token
            so that only authenticated users can access protected resources.
            Acceptance Criteria:
            - Tokens expire after 24 hours.
            - Refresh token flow is supported.
            - Invalid tokens return 401 with error detail.
            - Token blacklisting on logout.
        """),
        "complex": dedent("""
            US-088: Design multi-tenant schema with row-level security.
            As a platform architect I need the database to isolate tenant data
            with role-based access control and compliance audit logs.
            Acceptance Criteria:
            - Each tenant's data is fully isolated at the schema level.
            - RBAC enforced at DB row level via RLS policies.
            - All data access is logged with timestamp and user ID.
            - Schema migrations must not affect other tenants.
            - Supports GDPR right-to-erasure per tenant.
            - Performance: queries complete in < 200ms for 95th percentile.
        """),
        "coding_heavy": dedent("""
            US-112: Build the Playwright E2E test suite for the checkout flow.
            As a QA engineer I need a full Playwright test covering the checkout
            pipeline including payment, inventory update, and email confirmation.
            Acceptance Criteria:
            - Test covers happy path: add to cart → checkout → payment → confirm.
            - Tests run in CI/CD pipeline (GitHub Actions).
            - Mock Stripe API for payment simulation.
            - Parallel test execution across 3 browser engines.
            - Coverage report generated per run.
            - Failed test screenshots captured automatically.
        """),
    }

    print("\n" + "═" * 60)
    print("  Complexity Scorer — Demo Run")
    print("═" * 60)

    scorer = ComplexityScorer()

    for label, story in _DEMOS.items():
        print(f"\n▶  Story: {label.upper()}")
        print(f"   Text preview: {story.strip()[:80]}...")
        result = scorer.score(story)
        print(f"   Tier       : {result.tier.value}")
        print(f"   Model      : {result.recommended_model}")
        print(f"   Confidence : {result.confidence:.1%}")
        print(f"   LLM used   : {result.llm_used}")
        print(f"   Signals    : {', '.join(result.signals[:4]) or 'none'}")
        if result.llm_reason:
            print(f"   LLM reason : {result.llm_reason}")
        time.sleep(0.1)

    print("\n" + "═" * 60)
    print("  Integration example:")
    print("  model, result = get_model_for_story(story.full_text)")
    print("═" * 60 + "\n")
