"""
classifier_feedback.py — BRN-002: Request Classifier Feedback Loop

Tracks every classification decision along with the eventual story outcome
(DONE/FAILED). Over time this builds a picture of classifier precision per
category, enabling data-driven pattern tuning and future ML replacement.

Data model (JSONL, one entry per execution):
    {
        "ts":           "2025-03-05T06:12:00.000000",
        "story_id":     "US-042",
        "classified_as": "RC-002",   # request type enum value
        "category_label": "LLM_GENERATION",
        "outcome":       "DONE",     # DONE | FAILED | BLOCKED | SKIPPED
        "model_used":    "gemini-flash",
        "duration_s":    12.4,
        "tokens_used":   1840,
        "cost_usd":      0.0023
    }
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_LOG_DIR = Path(".routing_data/classifier_feedback")


class ClassifierFeedback:
    """
    Records and analyses classifier decisions vs execution outcomes.
    Thread-safe (atomic append via OS-level write).
    """

    def __init__(self, log_dir: str | Path = DEFAULT_LOG_DIR):
        self.log_dir  = Path(log_dir)
        self.log_file = self.log_dir / "feedback.jsonl"
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError) as e:
            logger.warning("[CLASSIFIER FB] Could not create log dir: %s", e)

    # ── Public API ─────────────────────────────────────────────────────────

    def record(
        self,
        story_id:       str,
        classified_as:  str,
        category_label: str,
        outcome:        str,
        model_used:     str = "unknown",
        duration_s:     float = 0.0,
        tokens_used:    int   = 0,
        cost_usd:       float = 0.0,
    ) -> None:
        """Append one feedback entry to the JSONL log."""
        entry = {
            "ts":             datetime.utcnow().isoformat(),
            "story_id":       story_id,
            "classified_as":  classified_as,
            "category_label": category_label,
            "outcome":        outcome,
            "model_used":     model_used,
            "duration_s":     round(duration_s, 2),
            "tokens_used":    tokens_used,
            "cost_usd":       round(cost_usd, 6),
        }
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as exc:
            logger.warning("[CLASSIFIER FB] Write failed: %s", exc)

    def get_precision_stats(self) -> Dict[str, dict]:
        """
        Return precision stats per classifier category:
            { "RC-002": { "total": 12, "done": 9, "failed": 3, "precision": 0.75 } }
        """
        entries = self._read_all()
        stats: Dict[str, dict] = {}
        for e in entries:
            key = e.get("classified_as", "UNKNOWN")
            if key not in stats:
                stats[key] = {
                    "category_label": e.get("category_label", key),
                    "total": 0, "done": 0, "failed": 0, "skipped": 0, "blocked": 0,
                }
            stats[key]["total"] += 1
            outcome = str(e.get("outcome", "UNKNOWN")).upper()
            if outcome == "DONE":
                stats[key]["done"] += 1
            elif outcome == "FAILED":
                stats[key]["failed"] += 1
            elif outcome == "SKIPPED":
                stats[key]["skipped"] += 1
            elif outcome == "BLOCKED":
                stats[key]["blocked"] += 1

        for key, s in stats.items():
            total = s["total"]
            s["precision"] = round(s["done"] / total, 3) if total else 0.0
            s["avg_cost_usd"] = 0.0  # filled below

        # Compute avg cost per category
        for e in entries:
            key = e.get("classified_as", "UNKNOWN")
            if key in stats:
                # running sum, we'll divide later
                stats[key]["avg_cost_usd"] = round(
                    stats[key].get("avg_cost_usd", 0.0) + e.get("cost_usd", 0.0), 6
                )
        for key, s in stats.items():
            total = s["total"]
            if total:
                s["avg_cost_usd"] = round(s["avg_cost_usd"] / total, 6)

        return stats

    def get_recent(self, last_n: int = 50) -> List[dict]:
        """Return the last N feedback entries (most recent first)."""
        entries = self._read_all()
        return list(reversed(entries[-last_n:]))

    def summary(self) -> dict:
        """Return a compact top-level summary for the dashboard."""
        entries = self._read_all()
        if not entries:
            return {"total_recorded": 0, "overall_precision": 0.0, "categories": {}}
        total = len(entries)
        done  = sum(1 for e in entries if str(e.get("outcome", "")).upper() == "DONE")
        stats = self.get_precision_stats()

        # Flag under-performing categories
        low_precision = [
            k for k, v in stats.items() if v["total"] >= 3 and v["precision"] < 0.60
        ]
        return {
            "total_recorded":    total,
            "overall_precision": round(done / total, 3),
            "categories":        stats,
            "low_precision_categories": low_precision,
        }

    # ── Private ────────────────────────────────────────────────────────────

    def _read_all(self) -> List[dict]:
        if not self.log_file.exists():
            return []
        entries = []
        try:
            with open(self.log_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except Exception as exc:
            logger.warning("[CLASSIFIER FB] Read failed: %s", exc)
        return entries
