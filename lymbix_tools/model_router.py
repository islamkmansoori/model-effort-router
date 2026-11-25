# Copied from the Lymbix project (lymbix/model_router.py, commit d305040, 2026-10-09). Apache-2.0, see LICENSE.
# Modified for model-effort-router on 2026-11-25: the two imports are relative to this package. Nothing else is changed.
"""
Model Router — US-058.

Determines the optimal model tier (Fast, Balanced, Premium) for a given task
based on its complexity, ensuring we use cheap models for simple tasks and
capable models for complex reasoning.
"""

import re
from enum import Enum
from typing import Any, Dict, List, Tuple

from .config import load_config
from .request_classifier import RequestClassifier, RequestType


class ModelTier(Enum):
    FAST = "fast"  # Gemini Flash, GPT-4o-mini
    BALANCED = "balanced"  # Gemini Pro, Claude Sonnet, GPT-4o
    PREMIUM = "premium"  # Gemini Ultra, Claude Opus, Reasoning models


class ModelRouter:
    """
    Routes user stories to the appropriate model tier.
    """

    def __init__(self, config: Dict = None):
        self.config = config or load_config()
        self.classifier = RequestClassifier()

        # Keywords that bump complexity to PREMIUM
        self.premium_keywords = [
            r"architecture",
            r"security",
            r"audit",
            r"refactor\s+all",
            r"migration",
            r"deployment",
            r"optimization",
            r"complex",
            r"design\s+pattern",
            r"concurrency",
            r"distributed",
            r"async",
        ]

        # Keywords that allow downgrading to FAST
        self.fast_keywords = [
            r"fix\s+typo",
            r"update\s+doc",
            r"comment",
            r"formatting",
            r"readme",
            r"simple",
            r"config\s+change",
            r"variable\s+rename",
        ]

        # Initialize tier mappings with defaults
        self.tier_keywords = {
            ModelTier.FAST: [
                "flash",
                "haiku",
                "lite",
                "nano",
                "turbo",
                "gpt-4o-mini",
                "o1-mini",
                "llama-3-8b",
                "llama-3.1-8b",
                "llama-3.2-1b",
                "llama-3.2-3b",
                "llama-4-maverick",
                "local",
                "lmstudio",
            ],
            ModelTier.PREMIUM: [
                "ultra",
                "opus",
                "thinking",
                "reasoning",
                "advanced",
                "qwq-32b",
                "deepseek-reasoner",
                "o1-preview",
                "mistral-large",
            ],
            ModelTier.BALANCED: [
                "pro",
                "sonnet",
                "gpt-4o",
                "balanced",
                "standard",
                "deepseek-chat",
                "mistral-large",
                "llama-3.3-70b",
            ],
        }

        # Specialized Coder Keywords (US-060)
        self.coder_keywords = ["coder", "deepseek-v2", "qwen2.5", "codestral"]

        # Override with config if present
        if self.config.get("model_routing"):
            cfg_tiers = self.config["model_routing"].get("tiers", {})
            for tier_name, markers in cfg_tiers.items():
                try:
                    tier_enum = ModelTier(tier_name.lower())
                    # Extend or replace? Replace allows full control.
                    self.tier_keywords[tier_enum] = markers
                except ValueError:
                    pass

    def route(self, story_content: str, story_id: str = "") -> Tuple[ModelTier, str]:
        """
        Determine the best model tier for a story.

        Returns:
            (ModelTier, reason_string)
        """
        # 0. Check for forced override (CLI flag or config)
        forced = self.config.get("force_model_tier")
        if forced:
            try:
                # Handle both string and Enum values
                tier_val = forced.value if isinstance(forced, ModelTier) else forced.lower()
                return ModelTier(tier_val), "Forced by CLI/config override"
            except ValueError:
                pass  # Invalid tier, fall back to auto-detection

        # 1. Classify request type
        req_type = self.classifier.classify(story_content, story_id)

        # 2. Check for complexity keywords
        content_lower = story_content.lower()

        is_premium = any(re.search(p, content_lower) for p in self.premium_keywords)
        is_fast = any(re.search(p, content_lower) for p in self.fast_keywords)

        # 3. Decision Logic

        # Critical/Reasoning tasks -> PREMIUM
        if req_type in (RequestType.LLM_REASONING, RequestType.LLM_REFACTORING):
            if is_fast and not is_premium:
                return ModelTier.BALANCED, "Reasoning task but simple scope"
            return ModelTier.PREMIUM, f"Complex {req_type.value} task"

        # Tool tasks -> FAST (if LLM is needed at all)
        if req_type.name.startswith("TOOL_"):
            return ModelTier.FAST, "Tool-assisted task (low cognitive load)"

        # Meta tasks -> FAST
        if req_type == RequestType.META_GROOMING or (
            story_id and (story_id.startswith("REQ_") or story_id.startswith("AGILE_"))
        ):
            return ModelTier.FAST, "Agile/Meta task (low reasoning complexity)"

        # Generation tasks
        if req_type == RequestType.LLM_GENERATION:
            if any(k in content_lower for k in self.coder_keywords):
                return ModelTier.PREMIUM, "Coding task — prioritizing Coder specialized model"
            if is_premium:
                return ModelTier.PREMIUM, "Generation task with complex requirements"
            if is_fast:
                return ModelTier.FAST, "Simple generation task"
            return ModelTier.BALANCED, "Standard generation task"

        return ModelTier.BALANCED, "Default routing"

    def route_from_complexity(self, complexity_result: Any) -> Tuple[ModelTier, str]:
        """
        Derive ModelTier directly from a ComplexityResult (from ComplexityScorer).

        This is the PREFERRED routing path — it skips the regex-pattern classification
        entirely and uses the already-computed complexity tier. Called by execute_story()
        when complexity_result is passed in from the Executor.

        Mapping:
          SIMPLE       → FAST      (Groq Llama, Gemini Flash)
          MID          → FAST      (still cheap; mid complexity ≠ needs premium model)
          COMPLEX      → BALANCED  (Gemini Pro, Claude Haiku, GPT-4o)
          CODING_HEAVY → PREMIUM   (Claude Sonnet, Antigravity bridge)

        Returns: (ModelTier, reason_string)
        """
        # Check for forced override first (respects CLI/config)
        forced = self.config.get("force_model_tier")
        if forced:
            try:
                tier_val = forced.value if isinstance(forced, ModelTier) else str(forced).lower()
                return ModelTier(tier_val), "Forced by CLI/config override"
            except (ValueError, AttributeError):
                pass

        tier_name = getattr(complexity_result, "tier", None)
        if tier_name is None:
            return ModelTier.BALANCED, "No ComplexityResult tier — default BALANCED"

        # ComplexityTier enum value is a string: "simple", "mid", "complex", "coding_heavy"
        tier_str = tier_name.value if hasattr(tier_name, "value") else str(tier_name)
        model = getattr(complexity_result, "recommended_model", "")
        confidence = getattr(complexity_result, "confidence", 0.0)

        mapping = {
            "simple": (ModelTier.FAST, f"SIMPLE complexity ({confidence:.0%} confidence) → FAST models"),
            "mid": (ModelTier.FAST, f"MID complexity ({confidence:.0%} confidence) → FAST models"),
            "complex": (ModelTier.BALANCED, f"COMPLEX complexity ({confidence:.0%} confidence) → BALANCED models"),
            "coding_heavy": (
                ModelTier.PREMIUM,
                f"CODING_HEAVY complexity ({confidence:.0%} confidence) → PREMIUM models",
            ),
        }

        return mapping.get(tier_str, (ModelTier.BALANCED, f"Unknown tier '{tier_str}' — default BALANCED"))

    def sort_models_by_tier(self, models: List, target_tier: ModelTier) -> List:
        """
        Sort a list of ModelConfig objects based on their suitability for the target tier.

        Args:
            models: List of ModelConfig objects
            target_tier: The desired ModelTier (FAST, BALANCED, PREMIUM)

        Returns:
            Sorted list of ModelConfig objects (best matches first)
        """
        target_markers = self.tier_keywords.get(target_tier, [])
        balanced_markers = self.tier_keywords.get(ModelTier.BALANCED, [])
        premium_markers = self.tier_keywords.get(ModelTier.PREMIUM, [])

        def exact_match_score(model) -> int:
            """Score 3=Exact Tier Match, 2=Acceptable Fallback, 1=Any Available, 0=Avoid"""
            name = (getattr(model, "model", "") + " " + getattr(model, "label", "")).lower()

            score = 1
            # Check for direct tier match
            if any(m in name for m in target_markers):
                score = 3
                # Bonus for Coder model on coding tasks
                if target_tier in (ModelTier.PREMIUM, ModelTier.BALANCED) and any(
                    k in name for k in self.coder_keywords
                ):
                    score += 1

            # Fallback logic
            elif target_tier == ModelTier.PREMIUM:
                # Fallback to balanced if premium unavailable
                if any(m in name for m in balanced_markers):
                    score = 2
            elif target_tier == ModelTier.FAST:
                # Ideally avoid slow models for fast tasks, but accept balanced
                if any(m in name for m in balanced_markers):
                    score = 1
            elif target_tier == ModelTier.BALANCED:
                # Accept premium as fallback for balanced tasks
                if any(m in name for m in premium_markers):
                    score = 2

            return score

        # Sort descending by score
        return sorted(models, key=exact_match_score, reverse=True)
