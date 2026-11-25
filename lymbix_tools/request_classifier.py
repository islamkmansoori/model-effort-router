"""
Request Classifier for Lymbix CLI.
Routes requests to LLM or local tools based on pattern matching.
Saves tokens by ensuring deterministic tasks never reach the LLM.

Implements: US-055 (Build Request Classifier)
Implements: Lymbix-RC-001 through Lymbix-RC-006 (BRD Version 1.0)
"""

import json
import re
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List

try:
    from rich.console import Console

    console = Console()
except ImportError:

    class Console:
        def __init__(self, *args, **kwargs):
            pass

        def print(self, *args, **kwargs):
            print(*args)

        def log(self, *args, **kwargs):
            print(*args)

    console = Console()


# Legacy V1 Taxonomy
class RequestType(Enum):
    """Classification of how a request should be handled (Legacy)."""

    LLM_GENERATION = "llm_generation"
    LLM_REFACTORING = "llm_refactoring"
    LLM_REASONING = "llm_reasoning"
    TOOL_LINTING = "tool_linting"
    TOOL_TESTING = "tool_testing"
    TOOL_DIFF = "tool_diff"
    TOOL_GIT = "tool_git"
    TOOL_DOCKER = "tool_docker"
    META_GROOMING = "meta_grooming"


# V2 Taxonomy Definition (FR-008)
TAXONOMY_V2 = {
    "RC-001": "Data Query",
    "RC-002": "Action Request",
    "RC-003": "Workflow Trigger",
    "RC-004": "Escalation",
    "RC-005": "Clarification",
    "RC-006": "Status Inquiry",
    "RC-007": "Feedback / Report",
    "RC-008": "Scheduling",
    "RC-009": "Authorization",
    "RC-010": "External Integration",
}


# Failure Taxonomy for Root Cause Engine
FAILURE_TAXONOMY = {
    "api_bridge": "API Bridge Integration",
    "db_schema": "Database Schema",
    "proxy_config": "Proxy Configuration",
    "test_logic": "Test Logic",
    "stale_story": "Stale Story",
    "unknown": "Unknown",
}

class RequestClassifier:
    """
    Classifies incoming requests to determine if LLM or local tools should handle them.
    Supports both V1 Legacy (RequestType enum) and V2 Structured Payloads (JSON compliant).
    """

    TOOL_PATTERNS: Dict[RequestType, List[str]] = {
        RequestType.TOOL_LINTING: [
            r"\blint\b",
            r"check\s+style",
            r"format\s+code",
            r"\bpep8\b",
            r"\beslint\b",
            r"\bruff\b",
            r"\bpylint\b",
            r"code\s+style",
            r"formatting",
        ],
        RequestType.TOOL_TESTING: [
            r"\brun\s+test",
            r"\bexecute\s+test",
            r"test\s+coverage",
            r"\bpytest\b",
            r"\bjest\b",
            r"\bunit\s+test",
            r"\be2e\s+test",
            r"integration\s+test",
            r"run\s+coverage",
            r"show\s+tests",
        ],
        RequestType.TOOL_DIFF: [
            r"\bgit\s+diff\b",
            r"compare\s+files",
            r"what\s+changed",
            r"show\s+changes",
            r"file\s+diff",
            r"diff\s+log",
        ],
        RequestType.TOOL_GIT: [
            r"\bgit\s+commit\b",
            r"\bgit\s+push\b",
            r"\bgit\s+branch\b",
            r"\bgit\s+stash\b",
            r"commit\s+message",
            r"\bgit\s+status\b",
            r"check\s+git",
        ],
        RequestType.TOOL_DOCKER: [
            r"\bdocker\s+build\b",
            r"\bdocker\s+run\b",
            r"\bdocker\s+compose\b",
            r"\bcontainer\b.*\bstart\b",
            r"stop\s+container",
        ],
    }

    LLM_PATTERNS: Dict[RequestType, List[str]] = {
        RequestType.LLM_GENERATION: [
            r"generate\s+.*code",
            r"create\s+.*function",
            r"write\s+.*class",
            r"implement\s+.*feature",
            r"build\s+.*component",
            r"add\s+.*endpoint",
            r"create\s+.*module",
            r"implement\s+.*api",
        ],
        RequestType.LLM_REFACTORING: [
            r"\brefactor\b",
            r"improve\s+.*code",
            r"optimize\s+.*implementation",
            r"\brestructure\b",
            r"clean\s+up",
            r"simplify",
        ],
        RequestType.LLM_REASONING: [
            r"why\s+.*fail",
            r"explain\s+.*error",
            r"analyze\s+.*issue",
            r"debug\s+.*complex",
            r"root\s+cause",
            r"investigate",
            r"diagnose",
        ],
    }

    META_PATTERNS: Dict[RequestType, List[str]] = {
        RequestType.META_GROOMING: [r"groom", r"raw\s+requirement", r"backlog\s+management"],
    }

    # Patterns mapped to V2 Taxonomy
    TAXONOMY_PATTERNS: Dict[str, List[str]] = {
        "RC-001": [
            r"get\s",
            r"fetch\s",
            r"query\s",
            r"lookup\s",
            r"what is",
            r"show me",
            r"list\s",
            r"read\s",
            r"show\s",
            r"display\s",
        ],
        "RC-002": [
            r"create\s",
            r"update\s",
            r"delete\s",
            r"run\s",
            r"execute\s",
            r"implement",
            r"build",
            r"write",
            r"add\s",
            r"change\s",
            r"modify\s",
        ],
        "RC-003": [r"pipeline", r"workflow", r"trigger", r"deploy", r"ci/cd", r"start\s"],
        "RC-004": [r"escalate", r"human", r"manager", r"review required", r"help me"],
        "RC-005": [r"unclear", r"please clarify", r"what do you mean", r"confused"],
        "RC-006": [r"status", r"is it done", r"where are we", r"progress", r"check\s", r"verify\s"],
        "RC-007": [r"feedback", r"issue", r"bug", r"report", r"broken", r"fix\s", r"debug\s", r"error"],
        "RC-008": [r"schedule", r"calendar", r"book", r"time", r"when\s"],
        "RC-009": [r"authorize", r"approve", r"permission", r"access", r"token", r"key"],
        "RC-010": [r"jira", r"github", r"slack", r"external", r"api", r"webhook", r"sync"],
    }

    def __init__(self, confidence_threshold: float = 0.75):
        self._history: List[Dict] = []
        self.confidence_threshold = confidence_threshold
        self.model_version = "lymbix-classifier-v1.0"

    # --- V1 LEGACY IMPLEMENTATION ---

    def classify(self, request: str, story_id: str = "") -> RequestType:
        """Legacy V1 Method."""
        if story_id and story_id.startswith("GROOMING_"):
            return RequestType.META_GROOMING

        request_lower = str(request).lower()

        for request_type, patterns in self.META_PATTERNS.items():
            if self._matches_patterns(request_lower, patterns):
                self._record(request_type, request, "meta_pattern")
                return request_type

        for request_type, patterns in self.TOOL_PATTERNS.items():
            if self._matches_patterns(request_lower, patterns):
                self._record(request_type, request, "tool_pattern")
                return request_type

        for request_type, patterns in self.LLM_PATTERNS.items():
            if self._matches_patterns(request_lower, patterns):
                self._record(request_type, request, "llm_pattern")
                return request_type

        self._record(RequestType.LLM_GENERATION, request, "fallback")
        return RequestType.LLM_GENERATION

    def should_use_llm(self, request_type: RequestType) -> bool:
        return request_type.value.startswith("llm_")

    def is_tool_request(self, request_type: RequestType) -> bool:
        return request_type.value.startswith("tool_")

    def get_tool_command(self, request_type: RequestType) -> str:
        tool_map = {
            RequestType.TOOL_LINTING: "ruff check . || pylint lymbix",
            RequestType.TOOL_TESTING: "pytest --cov=lymbix tests/",
            RequestType.TOOL_DIFF: "git diff HEAD",
            RequestType.TOOL_GIT: "git status",
            RequestType.TOOL_DOCKER: "docker ps -a",
        }
        return tool_map.get(request_type, "")

    def get_classification_stats(self) -> Dict:
        """Legacy stats."""
        total = len(self._history)
        if total == 0:
            return {"total": 0, "llm_ratio": 0.0, "tool_ratio": 0.0}

        llm_count = sum(1 for h in self._history if h.get("type", "").startswith("llm_"))
        tool_count = sum(1 for h in self._history if h.get("type", "").startswith("tool_"))
        fallback_count = sum(1 for h in self._history if h.get("reason") == "fallback")

        return {
            "total": total,
            "llm_count": llm_count,
            "tool_count": tool_count,
            "fallback_count": fallback_count,
            "llm_ratio": llm_count / total,
            "tool_ratio": tool_count / total,
            "fallback_ratio": fallback_count / total,
        }

    # --- V2 STRUCTURED PAYLOAD IMPLEMENTATION (BRD Compliant) ---

    def process_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Accepts a JSON-compliant payload (FR-002) and returns a structured
        Classification Result (FR-004 to FR-016).
        """
        st_time = datetime.now()

        # FR-003 Validation
        if not payload.get("request_id") or not payload.get("raw_input"):
            return {"error": "Malformed payload. Required fields: request_id, raw_input", "code": 400}

        req_id = payload.get("request_id")
        raw_input = payload.get("raw_input", "")
        # Handle dict raw_input
        if isinstance(raw_input, dict):
            raw_input = json.dumps(raw_input)

        raw_input_lower = str(raw_input).lower()

        # Multi-label taxonomy detection
        matched_types = []
        for rc_id, patterns in self.TAXONOMY_PATTERNS.items():
            if self._matches_patterns(raw_input_lower, patterns):
                matched_types.append(rc_id)

        # Calculate heuristics for confidence and priority
        primary_type = matched_types[0] if matched_types else "RC-002"  # Default to Action Request
        secondary_types = matched_types[1:] if len(matched_types) > 1 else []

        # Fake "confidence score" heuristic based on length and matches
        base_confidence = 0.5
        if matched_types:
            base_confidence += 0.3
        if len(raw_input) > 20:
            base_confidence += 0.1
        if len(matched_types) > 2:
            base_confidence -= 0.1  # too ambiguous

        confidence_score = min(max(base_confidence, 0.0), 1.0)

        # Determine Routing and Escalation
        primary_target = "orchestrator"
        secondary_targets = []
        fallback_triggered = False

        if confidence_score < self.confidence_threshold:
            fallback_triggered = True
            primary_target = "clarification-agent"
            secondary_types.append("RC-005")  # Tag as Clarification needed

        priority = "Medium"
        if "critical" in raw_input_lower or primary_type == "RC-004":
            priority = "Critical"
        elif "urgent" in raw_input_lower:
            priority = "High"

        latency_ms = int((datetime.now() - st_time).total_seconds() * 1000)

        # Build Output Schema (FR-020 etc)
        result = {
            "request_id": req_id,
            "classification": {
                "primary_type": primary_type,
                "primary_type_label": TAXONOMY_V2.get(primary_type, "Unknown"),
                "secondary_types": secondary_types,
                "intent": f"Detected {len(matched_types)} intent markers",
                "domain": "Engineering",
                "priority": priority,
                "urgency": priority in ["High", "Critical"],
                "confidence_score": round(confidence_score, 2),
            },
            "routing": {"primary_target": primary_target, "secondary_targets": secondary_targets},
            "fallback_triggered": fallback_triggered,
            "model_version": self.model_version,
            "classified_at": datetime.utcnow().isoformat() + "Z",
            "latency_ms": latency_ms,
        }

        # Log event (FR-020)
        self._history.append(result)

        return result

    def classify_failure(self, failure_message: str) -> str:
        """Classifies an execution failure into a root cause category (US-403)."""
        msg = str(failure_message).lower()
        if any(kw in msg for kw in ["api", "bridge", "endpoint", "proxy error", "network"]):
            return "api_bridge"
        if any(kw in msg for kw in ["database", "schema", "table", "column", "sql"]):
            return "db_schema"
        if any(kw in msg for kw in ["proxy", "config", "caddy", "yaml", "port"]):
            return "proxy_config"
        if any(kw in msg for kw in ["test", "pytest", "playwright", "assert", "mock"]):
            return "test_logic"
        if any(kw in msg for kw in ["stale", "backlog", "outdated"]):
            return "stale_story"
        return "unknown"

    # --- INTERNAL UTILS ---


    def _matches_patterns(self, text: str, patterns: List[str]) -> bool:
        return any(re.search(pattern, text) for pattern in patterns)

    def _record(self, request_type: RequestType, request: str, reason: str):
        self._history.append(
            {
                "type": request_type.value,
                "reason": reason,
                "request_preview": request[:80],
            }
        )
