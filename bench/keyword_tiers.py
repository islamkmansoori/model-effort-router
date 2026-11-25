"""A keyword-tier scorer, kept here as a baseline to compare against.

This is the rule engine of an earlier project by the same author (the original is in
routing_tools/complexity_scorer.py, and a test keeps the two identical): words such as "button", "form" and "list"
pull a task down, words such as "architecture", "algorithm", "kubernetes" and "concurrent" push it up, and the
total lands in one of four tiers. It was written for user stories and tuned by feel. The benchmark
asks the question it never faced: do its tiers predict which tasks need the stronger model?

On the 21 SWE-bench Verified tasks here they do not, reliably: starting its top tier on Opus costs more than
starting everything cheap and jumping to Opus on failure (see results/REPORT.md). It stays in the repository so
that anyone can rerun the comparison, and try their own keyword list against the same runs.
"""
from __future__ import annotations

import re
from typing import List, Tuple

TIERS = ["simple", "mid", "complex", "coding_heavy"]

# (name, pattern, score): each pattern counts at most twice. Positive scores push the task up.
RULES: List[Tuple[str, str, float]] = [
    ("crud_keywords", r"\b(crud|create|read|update|delete|list|fetch|get|put|post|form|field|table|basic|simple|display|show)\b", -0.12),
    ("ui_only", r"\b(button|modal|dropdown|navbar|sidebar|icon|tooltip|label|layout|color|style|css|theme|responsive)\b", -0.10),
    ("api_integration", r"\b(api|endpoint|rest|graphql|webhook|http|request|response|payload|header|auth(entication)?|jwt|oauth)\b", 0.10),
    ("business_logic", r"\b(rule|workflow|validat|calculat|transform|process|pipeline|condition|branch|decision|trigger|event)\b", 0.12),
    ("architecture", r"\b(architect|schema|design|model|relation|normaliz|microservice|service|component|module|layer|abstraction|pattern|dependency|inject)\b", 0.25),
    ("multi_system", r"\b(integrat|sync|async|queue|kafka|rabbitmq|redis|cache|message|broker|event.driven|distributed|multi.tenant|scalab)\b", 0.28),
    ("security", r"\b(secur|encrypt|decrypt|hash|permission|role|rbac|acl|audit|compliance|gdpr|pii|vulnerab)\b", 0.22),
    ("algorithm_heavy", r"\b(algorithm|optimize|performance|O\(|complexity|sort|search|graph|tree|dynamic.program|recursive|concurrent|parallel|thread|async await|coroutine)\b", 0.35),
    ("deep_coding", r"\b(write.*function|build.*class|build.*module|build.*sdk|write.*parser|write.*compiler|write.*library|implement.*algorithm|build.*cli|serialize|deserializ|write.*middleware|build.*engine)\b", 0.30),
    ("infra_or_devops", r"\b(docker|kubernetes|ci.cd|pipeline|terraform|deploy|infra|cloud|aws|gcp|azure|serverless|container|helm|nginx|load.balanc)\b", 0.32),
    ("architectural_design", r"\b(architecture|pattern|framework|design.system|refactor|decouple|interface|abstract|orchestrat|governance)\b", 0.38),
    ("premium_hint", r"\b(premium|thinking|high.reasoning|sota|gpt-4|claude-3|gemini-3)\b", 0.45),
    ("test_heavy", r"\b(test|playwright|cypress|jest|pytest|unit.test|integration.test|e2e|coverage|mock|fixture|stub)\b", 0.20),
]
THRESHOLDS = [(0.40, "coding_heavy"), (0.20, "complex"), (0.05, "mid"), (-9.99, "simple")]


def criteria_like_lines(text: str) -> int:
    bullets = len(re.findall(r"^\s*[-*•]\s+\S", text, re.M))
    explicit = len(re.findall(r"\b(given|when|then|ac\s*\d+|acceptance criteria)\b", text, re.I))
    return max(bullets, explicit)


def tier(text: str) -> Tuple[str, float]:
    """The tier and the raw score of a task text."""
    text = text or ""
    total = 0.0
    if len(text.split()) < 80:
        total -= 0.15
    n = criteria_like_lines(text)
    if n <= 2:
        total -= 0.12
    elif n <= 5:
        total += 0.15
    else:
        total += 0.20
    for _name, pattern, score in RULES:
        hits = len(re.findall(pattern, text, re.I))
        if hits:
            total += score * min(hits, 2)
    for at_least, name in THRESHOLDS:
        if total >= at_least:
            return name, round(total, 3)
    return "simple", round(total, 3)
