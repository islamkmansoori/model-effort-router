"""
Heuristic Complexity Classifier for Lymbix.
Determines Model Tier (LIGHT/MEDIUM/HEAVY) without calling an external LLM.
Implements US-013 requirements.
"""

import re
from enum import Enum
from typing import List, Optional


class ModelTier(Enum):
    LIGHT = "LIGHT"
    MEDIUM = "MEDIUM"
    HEAVY = "HEAVY"


class ComplexityClassifier:
    """
    Fast heuristic-based classifier for prompt complexity.
    Optimized for speed (< 50ms) as per LAW 5.
    """

    HEAVY_KEYWORDS = [
        r"\brefactor\b",
        r"\bmigrate\b",
        r"\brestructure\b",
        r"\barchitecture\b",
        r"\bcomplex\b",
        r"\bperformance\b",
        r"\bbottle\-?neck\b",
    ]

    def __init__(self, token_estimate_factor: int = 4):
        self.token_estimate_factor = token_estimate_factor

    def estimate_tokens(self, text: str) -> int:
        """Rough token estimation based on character count / factor."""
        return len(text) // self.token_estimate_factor

    def classify(self, prompt: str, context_files: Optional[List[str]] = None) -> ModelTier:
        """
        Classifies prompt into a ModelTier based on heuristics.
        """
        token_count = self.estimate_tokens(prompt)
        file_count = len(context_files) if context_files else 0
        prompt_lower = prompt.lower()

        # Rule 1: Keyword Escalation
        for pattern in self.HEAVY_KEYWORDS:
            if re.search(pattern, prompt_lower):
                # Keywords automatically push to at least MEDIUM
                if token_count > 1000 or file_count > 1:
                    return ModelTier.HEAVY
                return ModelTier.MEDIUM

        # Rule 2: Quantitative Heuristics
        if token_count > 2000 or file_count > 3:
            return ModelTier.HEAVY
        
        if (500 <= token_count <= 2000) or (1 <= file_count <= 3):
            return ModelTier.MEDIUM

        # Default
        return ModelTier.LIGHT
