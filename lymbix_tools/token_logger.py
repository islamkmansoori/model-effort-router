"""
Token Logger for Lymbix CLI.
Structured per-request logging of token usage, cost, and cache hits.
Data stored in JSONL format for easy analysis.

Implements: US-051 (Token Usage Dashboard)
"""
import json
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from pathlib import Path
import threading
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    console = Console()
except ImportError:
    class Console:
        def print(self, *args, **kwargs): print(*args)
    console = Console()
    class Table:
        def __init__(self, *args, **kwargs): pass
        def add_column(self, *args, **kwargs): pass
        def add_row(self, *args, **kwargs): pass
    class Panel:
        def __init__(self, *args, **kwargs): pass

from .safe_io import safe_append_text


class TokenLogger:
    """
    Logs token usage for analytics, monitoring, and cost tracking.

    Features:
    - Structured JSONL logging (one record per line)
    - Cost calculation with configurable pricing
    - Cache hit/miss tracking
    - Daily/weekly/monthly aggregation
    - Budget alerts
    - CSV export
    """

    # Default pricing (adjust per provider)
    DEFAULT_PRICING = {
        "input_tokens": 0.003 / 1000,    # $3 per million input
        "output_tokens": 0.015 / 1000,   # $15 per million output
        "cache_write": 0.00375 / 1000,   # $3.75 per million cache write
        "cache_read": 0.0003 / 1000,     # $0.30 per million cache read
    }

    def __init__(
        self,
        log_file: str = ".lymbix_data/token_usage.jsonl",
        pricing: Optional[Dict[str, float]] = None,
        budget: Optional[Dict[str, float]] = None,
    ):
        """
        Initialize token logger.

        Args:
            log_file: Path to JSONL log file
            pricing: Custom pricing overrides
            budget: Budget thresholds {daily, weekly, monthly}
        """
        self.log_file = Path(log_file)
        try:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError):
            pass
        self.pricing = pricing or self.DEFAULT_PRICING
        
        # Load budget from config (CST-002)
        from .config import load_config
        cfg = load_config()
        self.config_budget = cfg.get("budget", {
            "monthly_cap_inr": 500,
            "alert_threshold_pct": 0.8,
            "usd_to_inr_rate": 83.5
        })
        
        self.budget = budget or {
            "daily": 5.0,
            "weekly": 25.0,
            "monthly": self.config_budget.get("monthly_cap_inr", 500) / self.config_budget.get("usd_to_inr_rate", 83.5),
        }
        self._lock = threading.Lock()

    # ── Logging ───────────────────────────────────────────────────────────

    def log_request(
        self,
        request_type: str,
        input_tokens: int,
        output_tokens: int,
        cache_creation_tokens: int = 0,
        cache_read_tokens: int = 0,
        duration: float = 0.0,
        attempt: int = 1,
        success: bool = True,
        story_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        failure_reason: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Log a single LLM request.

        Returns:
            The log entry dictionary
        """
        cost = self._calculate_cost(
            input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens
        )
        
        # Lymbix-EPIC-006: Local LLM tier is always zero-cost
        if request_type == "call_local":
            cost = 0.0

        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "story_id": story_id,
            "request_type": request_type,
            "tokens": {
                "input": input_tokens,
                "output": output_tokens,
                "total": input_tokens + output_tokens,
                "cache_creation": cache_creation_tokens,
                "cache_read": cache_read_tokens,
                "effective_input": input_tokens - cache_read_tokens,
            },
            "cost_usd": cost,
            "duration_seconds": round(duration, 2),
            "attempt": attempt,
            "retries": attempt - 1,
            "success": success,
            "failure_reason": failure_reason,
            "cache_hit": cache_read_tokens > 0,
            "metadata": metadata or {},
            "model": model,
        }

        # Append to JSONL file
        with self._lock:
            safe_append_text(self.log_file, json.dumps(log_entry) + "\n")

        # Check budget
        self._check_budget_alert(cost)

        return log_entry

    def log_tool_request(
        self,
        tool_name: str,
        duration: float = 0.0,
        success: bool = True,
        story_id: str = "",
    ) -> Dict[str, Any]:
        """
        Log a request handled by a local tool (zero tokens).
        Useful for tracking how many requests were saved from LLM.
        """
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "story_id": story_id,
            "request_type": f"tool_{tool_name}",
            "tokens": {
                "input": 0, "output": 0, "total": 0,
                "cache_creation": 0, "cache_read": 0, "effective_input": 0,
            },
            "cost_usd": 0.0,
            "duration_seconds": round(duration, 2),
            "attempt": 1,
            "success": success,
            "cache_hit": False,
            "tool_delegated": True,
            "metadata": {"tool": tool_name},
        }

        safe_append_text(self.log_file, json.dumps(log_entry) + "\n")

        return log_entry

    def log(self, entry: Dict[str, Any]) -> None:
        """Generic log entry for structured data."""
        if "timestamp" not in entry:
            entry["timestamp"] = datetime.now().isoformat()
        
        with self._lock:
            safe_append_text(self.log_file, json.dumps(entry) + "\n")

    def log_complexity(self, story_id: str, complexity_data: Dict[str, Any]) -> None:
        """
        Log a ComplexityScorer result alongside the token log.
        This surfaces tier, confidence, signals, and model recommendation
        in the dashboard's History tab for each story execution.
        Non-critical: failures are silently ignored by the Executor.
        """
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "story_id": story_id,
            "request_type": "complexity_score",
            "tokens": {"input": 0, "output": 0, "total": 0,
                       "cache_creation": 0, "cache_read": 0, "effective_input": 0},
            "cost_usd": 0.0,
            "duration_seconds": 0.0,
            "attempt": 1,
            "success": True,
            "cache_hit": False,
            "metadata": complexity_data,
        }
        self.log(log_entry)

    # ── Aggregation & Reports ─────────────────────────────────────────────

    def get_leaderboard_stats(self) -> List[Dict[str, Any]]:
        """
        CST-001: Aggregate performance metrics across all models to generate the leaderboard.
        Calculates success rates, latency, and token efficiency.
        """
        models_data = {}
        entries = self._read_entries()

        for entry in entries:
            # We look for LLM request entries or final execution logs with attempts
            model = None
            success = entry.get("success", False)
            duration = entry.get("duration_seconds", 0.0)
            cost = entry.get("cost_usd", 0.0)
            tokens = entry.get("tokens", {}).get("total", 0)

            # Try to find model in metadata
            if "metadata" in entry:
                model = entry["metadata"].get("model")
                if not model and "attempts" in entry["metadata"]:
                    # If this is a final log with attempts, process each attempt separately for leaderboard
                    for att in entry["metadata"]["attempts"]:
                        att_model = att.get("model")
                        if not att_model: continue
                        
                        if att_model not in models_data:
                            models_data[att_model] = {
                                "name": att_model,
                                "total_requests": 0,
                                "successes": 0,
                                "total_duration": 0.0,
                                "total_cost": 0.0,
                                "total_tokens": 0,
                                "failures": 0
                            }
                        m_stats = models_data[att_model]
                        m_stats["total_requests"] += 1
                        m_stats["total_duration"] += att.get("duration", 0.0)
                        m_stats["total_cost"] += att.get("cost_usd", 0.0)
                        m_stats["total_tokens"] += att.get("tokens", 0)
                        if att.get("success"):
                            m_stats["successes"] += 1
                        else:
                            m_stats["failures"] += 1
                    continue # Already processed attempts

            if not model:
                # Fallback to request_type mapping
                rt = entry.get("request_type", "")
                if rt == "exec_antigravity": model = "Antigravity Agent"
                elif rt.startswith("tool_"): model = "Local Tool"
                else: continue # Skip non-model entries

            if model not in models_data:
                models_data[model] = {
                    "name": model,
                    "total_requests": 0,
                    "successes": 0,
                    "total_duration": 0.0,
                    "total_cost": 0.0,
                    "total_tokens": 0,
                    "failures": 0
                }
            
            m_stats = models_data[model]
            m_stats["total_requests"] += 1
            m_stats["total_duration"] += duration
            m_stats["total_cost"] += cost
            m_stats["total_tokens"] += tokens
            if success:
                m_stats["successes"] += 1
            else:
                m_stats["failures"] += 1

        leaderboard = []
        for name, d in models_data.items():
            total = d["total_requests"]
            if total == 0: continue
            
            success_rate = (d["successes"] / total) * 100
            avg_latency = d["total_duration"] / total
            avg_cost = d["total_cost"] / total
            avg_tokens = d["total_tokens"] / total

            # Calculation for Performance Index (PI)
            # Higher is better. 100 is "Perfect".
            # Formula: (SuccessRate * 0.7) + (Inverse Latency * 0.2) + (Inverse Cost * 0.1)
            # Normalized latency: cap at 30s. 0s = 1.0, 30s = 0.0
            norm_latency = max(0, (30 - avg_latency) / 30)
            # Normalized cost: cap at $0.05 per req. $0=1.0, $0.05=0.0
            norm_cost = max(0, (0.05 - avg_cost) / 0.05)
            
            performance_index = (success_rate * 0.7) + (norm_latency * 20) + (norm_cost * 10)

            leaderboard.append({
                "model": name,
                "requests": total,
                "success_rate": round(success_rate, 1),
                "avg_latency": round(avg_latency, 2),
                "avg_cost": round(avg_cost, 4),
                "avg_tokens": int(avg_tokens),
                "performance_index": round(performance_index, 1),
                "tier": "Elite" if performance_index > 90 else "Strong" if performance_index > 75 else "Stable" if performance_index > 50 else "Risky"
            })

        # Sort by Performance Index descending
        return sorted(leaderboard, key=lambda x: x["performance_index"], reverse=True)

    def get_daily_stats(self, date: Optional[str] = None) -> Dict[str, Any]:
        """Get aggregated stats for a specific date (YYYY-MM-DD)."""
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        return self._aggregate(lambda ts: ts.startswith(date), label=date)

    def get_weekly_stats(self) -> Dict[str, Any]:
        """Get aggregated stats for the last 7 days."""
        cutoff = (datetime.now() - timedelta(days=7)).isoformat()
        return self._aggregate(
            lambda ts: ts >= cutoff, label="last_7_days"
        )

    def get_monthly_stats(self) -> Dict[str, Any]:
        """Get aggregated stats for the last 30 days."""
        cutoff = (datetime.now() - timedelta(days=30)).isoformat()
        return self._aggregate(
            lambda ts: ts >= cutoff, label="last_30_days"
        )

    def get_stats_by_type(self) -> Dict[str, Dict[str, Any]]:
        """Group stats by request type."""
        by_type: Dict[str, Dict[str, Any]] = {}

        for entry in self._read_entries():
            rt = entry.get("request_type", "unknown")
            if rt not in by_type:
                by_type[rt] = {"count": 0, "tokens": 0, "cost": 0.0}
            by_type[rt]["count"] += 1
            by_type[rt]["tokens"] += entry.get("tokens", {}).get("total", 0)
            by_type[rt]["cost"] += entry.get("cost_usd", 0.0)

        return by_type

    def get_model_stats(self) -> Dict[str, Any]:
        """Aggregate stats by Model (derived from metadata)."""
        model_stats = {}
        total_tokens_all = 0

        for entry in self._read_entries():
            tokens = entry.get("tokens", {}).get("total", 0)
            total_tokens_all += tokens
            
            # Determine model
            model = "unknown"
            if "metadata" in entry:
                # Try direct model key first (for non-antigravity integrations)
                model = entry["metadata"].get("model") or "unknown"
                
                # If still unknown, check Lymbix attempt structure
                if model == "unknown" and "attempts" in entry["metadata"]:
                    attempts = entry["metadata"]["attempts"]
                    if attempts and isinstance(attempts, list) and len(attempts) > 0:
                        last_attempt = attempts[-1]
                        if isinstance(last_attempt, dict):
                            model = last_attempt.get("model") or "unknown"
            
            # Clean up model name
            if model == "unknown" and entry.get("request_type") == "exec_antigravity":
                model = "Antigravity Agent (Integrated)"
            elif model == "unknown" and entry.get("request_type", "").startswith("tool_"):
                model = "Tool (Local)"
            elif model == "unknown":
                model = "Unspecified LLM"

            if model not in model_stats:
                model_stats[model] = {"count": 0, "tokens": 0, "cost": 0.0}
            
            model_stats[model]["count"] += 1
            model_stats[model]["tokens"] += tokens
            model_stats[model]["cost"] += entry.get("cost_usd", 0.0)

        # Calculate percentages
        results = {}
        for m, data in model_stats.items():
            pct = round(data["tokens"] / total_tokens_all * 100, 1) if total_tokens_all > 0 else 0
            results[m] = {
                "count": data["count"],
                "tokens": data["tokens"],
                "cost": round(data["cost"], 4),
                "percentage": pct
            }

        return {
            "total_tokens": total_tokens_all,
            "by_model": results
        }

    # ── Dashboard (Rich CLI) ─────────────────────────────────────────────

    def display_dashboard(self):
        """Display a rich CLI token-usage dashboard."""
        daily = self.get_daily_stats()
        weekly = self.get_weekly_stats()
        monthly = self.get_monthly_stats()
        by_type = self.get_stats_by_type()

        # Summary table
        summary = Table(
            title="📊 Token Usage Dashboard",
            show_header=True,
            header_style="bold cyan",
        )
        summary.add_column("Period", style="bold")
        summary.add_column("Requests", justify="right")
        summary.add_column("Tokens", justify="right")
        summary.add_column("Cost", justify="right")
        summary.add_column("Avg Retries", justify="right")
        summary.add_column("Cache Hit %", justify="right")
        summary.add_column("Tool Saved", justify="right")

        for label, stats in [
            ("Today", daily),
            ("Last 7 Days", weekly),
            ("Last 30 Days", monthly),
        ]:
            summary.add_row(
                label,
                str(stats["total_requests"]),
                f"{stats['total_tokens']:,}",
                f"${stats['total_cost']:.4f}",
                f"{stats.get('avg_retries', 0):.2f}",
                f"{stats.get('cache_hit_rate', 0):.0%}",
                str(stats.get("tool_delegated", 0)),
            )

        console.print(summary)

        # By-type breakdown
        if by_type:
            type_table = Table(
                title="📋 Usage by Request Type",
                show_header=True,
                header_style="bold yellow",
            )
            type_table.add_column("Type", style="bold")
            type_table.add_column("Count", justify="right")
            type_table.add_column("Tokens", justify="right")
            type_table.add_column("Cost", justify="right")
            type_table.add_column("Avg Retries", justify="right")

            for rt, data in sorted(
                by_type.items(), key=lambda x: x[1]["cost"], reverse=True
            ):
                avg_retries = data.get("retries", 0) / data["count"] if data["count"] > 0 else 0
                type_table.add_row(
                    rt,
                    str(data["count"]),
                    f"{data['tokens']:,}",
                    f"${data['cost']:.4f}",
                    f"{avg_retries:.2f}",
                )

            console.print(type_table)

        # Failure & Flakiness Analytics
        if daily["flaky_stories"] or daily["failure_reasons"]:
            analytics_table = Table(
                title="🔍 Retry & Failure Analytics (Today)",
                show_header=True,
                header_style="bold magenta",
            )
            analytics_table.add_column("Category", style="bold")
            analytics_table.add_column("Details")
            analytics_table.add_column("Count", justify="right")

            # Top failure reasons
            for reason, count in sorted(daily["failure_reasons"].items(), key=lambda x: x[1], reverse=True)[:3]:
                analytics_table.add_row("Failure Reason", reason[:60], str(count))

            # Top flaky stories
            for sid, retries in sorted(daily["flaky_stories"].items(), key=lambda x: x[1], reverse=True)[:3]:
                analytics_table.add_row("Flaky Story", sid, f"{retries} retries")

            console.print(analytics_table)

        # Budget alerts
        for period_label, period_stats, budget_key in [
            ("Daily", daily, "daily"),
            ("Weekly", weekly, "weekly"),
            ("Monthly", monthly, "monthly"),
        ]:
            budget = self.budget.get(budget_key, 0)
            if budget > 0:
                pct = (period_stats["total_cost"] / budget) * 100
                if pct >= 100:
                    console.print(
                        f"[bold red]🚨 {period_label} budget EXCEEDED: "
                        f"${period_stats['total_cost']:.4f} / ${budget:.2f} ({pct:.0f}%) | BLOCKED[/bold red]"
                    )
                elif pct >= 75:
                    console.print(
                        f"[yellow]⚠️  {period_label} budget warning: "
                        f"${period_stats['total_cost']:.4f} / ${budget:.2f} ({pct:.0f}%)[/yellow]"
                    )

    def check_budget_enforcement(self) -> (bool, str):
        """
        Check if any budget threshold has been exceeded.
        Returns (allowed: bool, reason: str)
        """
        daily = self.get_daily_stats()
        daily_budget = self.budget.get("daily", 0)
        
        if daily_budget > 0 and daily["total_cost"] >= daily_budget:
            return False, f"Daily budget exceeded (${daily['total_cost']:.2f} >= ${daily_budget:.2f})"

        monthly = self.get_monthly_stats()
        monthly_budget = self.budget.get("monthly", 0)
        if monthly_budget > 0 and monthly["total_cost"] >= monthly_budget:
            inr_rate = self.config_budget.get("usd_to_inr_rate", 83.5)
            inr_cost = monthly['total_cost'] * inr_rate
            inr_cap = self.config_budget.get("monthly_cap_inr", 500)
            return False, f"Monthly budget exceeded (₹{inr_cost:.2f} >= ₹{inr_cap:.2f})"
            
        return True, "Budget OK"

    def check_budget_enforcement_inr(self) -> (bool, str):
        """Helper for INR focused enforcement."""
        return self.check_budget_enforcement()

    # ── Export ─────────────────────────────────────────────────────────────

    def export_csv(self, output_path: str = ".lymbix_data/token_usage.csv") -> str:
        """Export token usage to CSV."""
        import csv

        entries = self._read_entries()
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        with open(out, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp", "story_id", "request_type",
                "input_tokens", "output_tokens", "total_tokens",
                "effective_input", "cache_read", "cost_usd",
                "duration_seconds", "attempt", "success", "cache_hit",
            ])
            for e in entries:
                writer.writerow([
                    e["timestamp"], e.get("story_id", ""),
                    e["request_type"],
                    e["tokens"]["input"], e["tokens"]["output"],
                    e["tokens"]["total"], e["tokens"]["effective_input"],
                    e["tokens"]["cache_read"], e["cost_usd"],
                    e["duration_seconds"], e["attempt"],
                    e["success"], e.get("cache_hit", False),
                ])

        console.print(f"[green]📄 Exported {len(entries)} records to {out}[/green]")
        return str(out)

    # ── Internal Helpers ──────────────────────────────────────────────────

    def _calculate_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_creation_tokens: int,
        cache_read_tokens: int,
    ) -> float:
        """Calculate total cost in USD."""
        cost = (
            input_tokens * self.pricing["input_tokens"]
            + output_tokens * self.pricing["output_tokens"]
            + cache_creation_tokens * self.pricing["cache_write"]
            + cache_read_tokens * self.pricing["cache_read"]
        )
        return round(cost, 6)

    def _read_entries(self) -> List[Dict[str, Any]]:
        """Read all log entries from JSONL file."""
        entries = []
        try:
            if not self.log_file.exists():
                return []
            with open(self.log_file) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except (PermissionError, OSError):
            pass
        return entries

    def _aggregate(self, predicate, label: str = "") -> Dict[str, Any]:
        """Aggregate entries matching a timestamp predicate."""
        stats = {
            "label": label,
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "total_tokens": 0,
            "effective_tokens": 0,
            "total_cost": 0.0,
            "total_retries": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "tool_delegated": 0,
            "by_request_type": {},
            "failure_reasons": {},
            "flaky_stories": {}, 
        }

        for entry in self._read_entries():
            if not predicate(entry.get("timestamp") or ""):
                continue

            stats["total_requests"] += 1
            if entry.get("success"):
                stats["successful_requests"] += 1
            else:
                stats["failed_requests"] += 1
                reason = entry.get("failure_reason") or "Unknown"
                stats["failure_reasons"][reason] = stats["failure_reasons"].get(reason, 0) + 1

            stats["total_tokens"] += entry.get("tokens", {}).get("total", 0)
            stats["effective_tokens"] += entry.get("tokens", {}).get("effective_input", 0)
            stats["total_cost"] += entry.get("cost_usd", 0.0)
            
            retries = entry.get("retries", entry.get("attempt", 1) - 1)
            stats["total_retries"] += retries
            
            if retries > 0:
                sid = entry.get("story_id", "unknown")
                stats["flaky_stories"][sid] = stats["flaky_stories"].get(sid, 0) + retries

            if entry.get("cache_hit"):
                stats["cache_hits"] += 1
            else:
                stats["cache_misses"] += 1

            if entry.get("tool_delegated"):
                stats["tool_delegated"] += 1

            rt = entry.get("request_type", "unknown")
            if rt not in stats["by_request_type"]:
                stats["by_request_type"][rt] = {"count": 0, "tokens": 0, "cost": 0.0, "retries": 0}
            stats["by_request_type"][rt]["count"] += 1
            stats["by_request_type"][rt]["tokens"] += entry.get("tokens", {}).get("total", 0)
            stats["by_request_type"][rt]["cost"] += entry.get("cost_usd", 0.0)
            stats["by_request_type"][rt]["retries"] += retries

        total = stats["total_requests"]
        stats["cache_hit_rate"] = stats["cache_hits"] / total if total > 0 else 0.0
        stats["success_rate"] = stats["successful_requests"] / total if total > 0 else 0.0
        stats["avg_retries"] = stats["total_retries"] / total if total > 0 else 0.0

        return stats

    def _check_budget_alert(self, latest_cost: float):
        """Check if a budget threshold has been crossed."""
        daily = self.get_daily_stats()
        daily_budget = self.budget.get("daily", 0)
        if daily_budget > 0:
            pct = (daily["total_cost"] / daily_budget) * 100
            if pct >= 100:
                console.print(
                    f"[bold red]🚨 DAILY BUDGET EXCEEDED: "
                    f"${daily['total_cost']:.4f} / ${daily_budget:.2f}[/bold red]"
                )
            elif pct >= 75:
                console.print(
                    f"[yellow]⚠️  Daily budget warning: "
                    f"${daily['total_cost']:.4f} / ${daily_budget:.2f} ({pct:.0f}%)[/yellow]"
                )
        
        # Monthly INR check (CST-002)
        monthly = self.get_monthly_stats()
        inr_rate = self.config_budget.get("usd_to_inr_rate", 83.5)
        inr_cost = monthly["total_cost"] * inr_rate
        inr_cap = self.config_budget.get("monthly_cap_inr", 500)
        threshold_pct = self.config_budget.get("alert_threshold_pct", 0.8)
        
        if inr_cap > 0:
            pct = inr_cost / inr_cap
            if pct >= 1.0:
                 console.print(f"[bold red]🚨 MONTHLY INR BUDGET EXCEEDED: ₹{inr_cost:.2f} / ₹{inr_cap:.2f}[/bold red]")
            elif pct >= threshold_pct:
                 console.print(f"[bold yellow]⚠️  Monthly INR budget warning: ₹{inr_cost:.2f} / ₹{inr_cap:.2f} ({pct:.1%})[/bold yellow]")
