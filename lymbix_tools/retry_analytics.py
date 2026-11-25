"""
Retry Analytics Engine for Lymbix CLI.
Tracks retry patterns, failure rates, and root causes across stories.
Generates reports and alerts on high retry rates.

Implements: US-052 (Retry Analytics Dashboard)
"""
import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.prompt import Confirm
    console = Console()
except ImportError:
    class Console:
        def __init__(self, *args, **kwargs): pass
        def print(self, *args, **kwargs): print(*args)
        def log(self, *args, **kwargs): print(*args)
    console = Console()
    class Table:
        def __init__(self, *args, **kwargs): pass
        def add_column(self, *args, **kwargs): pass
        def add_row(self, *args, **kwargs): pass
    class Panel:
        def __init__(self, *args, **kwargs): pass
    class Confirm:
        @staticmethod
        def ask(msg, default=True): return default


class RetryAnalytics:
    """
    Analyses retry history to surface patterns and problem areas.

    Features:
    - Aggregate retry stats per story and per session
    - Identify top-retrying stories
    - Failure reason categorisation
    - Alert when aggregate retry rate exceeds threshold
    - Weekly retry report (Rich CLI)
    """

    ALERT_THRESHOLD = 0.10  # alert when >10% of executions are retries

    def __init__(self, history_dir: str = ".lymbix_data/retry_history"):
        self.history_dir = Path(history_dir)
        try:
            self.history_dir.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError):
            pass

    # ── Data Collection ───────────────────────────────────────────────────

    def get_all_histories(self) -> List[Dict[str, Any]]:
        """Load every story's retry history from disk."""
        histories: List[Dict[str, Any]] = []
        try:
            for history_file in self.history_dir.glob("*.json"):
                try:
                    data = json.loads(history_file.read_text())
                    histories.append(data)
                except (json.JSONDecodeError, Exception):
                    continue
        except (PermissionError, OSError):
            pass
        return histories

    # ── Aggregation ───────────────────────────────────────────────────────

    def get_aggregate_stats(self, since_days: Optional[int] = None) -> Dict[str, Any]:
        """
        Aggregate retry statistics across all stories.

        Args:
            since_days: Only count attempts from the last N days (None = all time)

        Returns:
            Aggregate stats dictionary
        """
        cutoff = None
        if since_days is not None:
            cutoff = (datetime.now() - timedelta(days=since_days)).isoformat()

        total_attempts = 0
        total_failures = 0
        total_successes = 0
        total_retries = 0  # attempts beyond the first per story
        total_tokens_wasted = 0
        stories_with_retries = 0
        stories_total = 0

        per_story: Dict[str, Dict[str, Any]] = {}

        for history in self.get_all_histories():
            story_id = history.get("story", "unknown")
            attempts = history.get("attempts", [])

            # Filter by cutoff
            if cutoff:
                attempts = [
                    a for a in attempts
                    if (a.get("timestamp") or "") >= cutoff
                ]

            if not attempts:
                continue

            stories_total += 1
            failures = [a for a in attempts if a.get("exit_code", 0) != 0]
            successes = [a for a in attempts if a.get("exit_code", 0) == 0]
            retries = max(0, len(attempts) - 1)

            total_attempts += len(attempts)
            total_failures += len(failures)
            total_successes += len(successes)
            total_retries += retries

            if retries > 0:
                stories_with_retries += 1
                total_tokens_wasted += sum(
                    a.get("tokens", 0) for a in failures
                )

            per_story[story_id] = {
                "attempts": len(attempts),
                "failures": len(failures),
                "successes": len(successes),
                "retries": retries,
                "tokens_wasted": sum(a.get("tokens", 0) for a in failures),
                "last_attempt": (attempts[-1].get("timestamp") or "") if attempts else "",
            }

        retry_rate = total_retries / total_attempts if total_attempts > 0 else 0.0

        return {
            "stories_total": stories_total,
            "stories_with_retries": stories_with_retries,
            "total_attempts": total_attempts,
            "total_failures": total_failures,
            "total_successes": total_successes,
            "total_retries": total_retries,
            "retry_rate": retry_rate,
            "tokens_wasted_on_failures": total_tokens_wasted,
            "per_story": per_story,
            "by_request_type": self.get_stats_by_request_type(cutoff),
            "alert": retry_rate > self.ALERT_THRESHOLD,
        }

    def get_top_retrying_stories(self, top_n: int = 5) -> List[Dict[str, Any]]:
        """Return the stories with the most retries."""
        stats = self.get_aggregate_stats()
        per_story = stats.get("per_story", {})

        sorted_stories = sorted(
            per_story.items(),
            key=lambda x: x[1]["retries"],
            reverse=True,
        )

        return [
            {"story_id": sid, **data}
            for sid, data in sorted_stories[:top_n]
            if data["retries"] > 0
        ]

    def get_failure_reasons(self) -> Dict[str, int]:
        """
        Categorise failures by reason or exit code.
        """
        reasons: Dict[str, int] = defaultdict(int)

        for history in self.get_all_histories():
            for attempt in history.get("attempts", []):
                if attempt.get("exit_code", 0) != 0:
                    reason = attempt.get("failure_reason")
                    if reason:
                        reasons[reason] += 1
                    else:
                        code = attempt["exit_code"]
                        if code == 1:
                            reasons["execution_failure"] += 1
                        elif code == 2:
                            reasons["validation_failure"] += 1
                        elif code == 137:
                            reasons["timeout_killed"] += 1
                        else:
                            reasons[f"exit_code_{code}"] += 1

        return dict(reasons)

    def get_stats_by_request_type(self, cutoff: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        """Aggregate retry stats by request type."""
        by_type: Dict[str, Dict[str, Any]] = {}

        for history in self.get_all_histories():
            attempts = history.get("attempts", [])
            if cutoff:
                attempts = [a for a in attempts if (a.get("timestamp") or "") >= cutoff]
            
            for a in attempts:
                rt = a.get("request_type", "unknown")
                if rt not in by_type:
                    by_type[rt] = {"attempts": 0, "failures": 0, "retries": 0}
                
                by_type[rt]["attempts"] += 1
                if a.get("exit_code", 0) != 0:
                    by_type[rt]["failures"] += 1
                
                # We count an attempt as a retry if it's not the first for this story
                if a.get("attempt", 1) > 1:
                    by_type[rt]["retries"] += 1
        
        return by_type

    # ── Alerting ──────────────────────────────────────────────────────────

    def check_alerts(self) -> List[str]:
        """Return alert messages if retry rate is too high."""
        alerts: List[str] = []
        stats = self.get_aggregate_stats()

        if stats["alert"]:
            alerts.append(
                f"🚨 Retry rate is {stats['retry_rate']:.0%} "
                f"(threshold: {self.ALERT_THRESHOLD:.0%}). "
                f"{stats['total_retries']} retries across "
                f"{stats['stories_with_retries']} stories."
            )

        # Per-story alerts: any story with ≥3 failures
        for sid, data in stats.get("per_story", {}).items():
            if data["failures"] >= 3:
                alerts.append(
                    f"⚠️  {sid} has failed {data['failures']} times "
                    f"({data['tokens_wasted']:,} tokens wasted)."
                )

        return alerts

    # ── Dashboard ─────────────────────────────────────────────────────────

    def display_dashboard(self, since_days: Optional[int] = 7):
        """Display a rich CLI retry analytics dashboard."""
        stats = self.get_aggregate_stats(since_days=since_days)
        top_retries = self.get_top_retrying_stories()
        reasons = self.get_failure_reasons()
        alerts = self.check_alerts()

        period = f"Last {since_days} days" if since_days else "All time"

        # ── Summary Panel ──
        summary = Table(
            show_header=False,
            box=None,
            padding=(0, 2),
        )
        summary.add_row(
            "📊 [bold]Total Stories[/bold]",
            f"[cyan]{stats['stories_total']}[/cyan]",
        )
        summary.add_row(
            "🔄 [bold]Total Attempts[/bold]",
            f"[cyan]{stats['total_attempts']}[/cyan]",
        )
        summary.add_row(
            "✅ [bold]Successes[/bold]",
            f"[green]{stats['total_successes']}[/green]",
        )
        summary.add_row(
            "❌ [bold]Failures[/bold]",
            f"[red]{stats['total_failures']}[/red]",
        )
        summary.add_row(
            "🔁 [bold]Retries[/bold]",
            f"[yellow]{stats['total_retries']}[/yellow]",
        )
        summary.add_row(
            "📈 [bold]Retry Rate[/bold]",
            f"[{'red' if stats['alert'] else 'green'}]"
            f"{stats['retry_rate']:.1%}[/{'red' if stats['alert'] else 'green'}]",
        )
        summary.add_row(
            "🔥 [bold]Tokens Wasted[/bold]",
            f"[red]{stats['tokens_wasted_on_failures']:,}[/red]",
        )

        console.print(Panel(
            summary,
            title=f"[bold]🔄 Retry Analytics — {period}[/bold]",
            border_style="blue",
            padding=(1, 2),
        ))

        # ── Top Retrying Stories ──
        if top_retries:
            retry_table = Table(
                title="🏆 Top Retrying Stories",
                show_header=True,
                header_style="bold yellow",
            )
            retry_table.add_column("Story", style="bold")
            retry_table.add_column("Retries", justify="right")
            retry_table.add_column("Failures", justify="right")
            retry_table.add_column("Tokens Wasted", justify="right")
            retry_table.add_column("Last Attempt")

            for story in top_retries:
                retry_table.add_row(
                    story["story_id"],
                    str(story["retries"]),
                    f"[red]{story['failures']}[/red]",
                    f"[red]{story['tokens_wasted']:,}[/red]",
                    story.get("last_attempt", "")[:16],
                )

            console.print(retry_table)

        # ── Usage by Request Type ──
        if stats.get("by_request_type"):
            type_table = Table(
                title="📋 Retries by Request Type",
                show_header=True,
                header_style="bold yellow",
            )
            type_table.add_column("Type", style="bold")
            type_table.add_column("Attempts", justify="right")
            type_table.add_column("Retries", justify="right")
            type_table.add_column("Rate", justify="right")

            for rt, data in sorted(
                stats["by_request_type"].items(), key=lambda x: x[1]["retries"], reverse=True
            ):
                rate = data["retries"] / data["attempts"] if data["attempts"] > 0 else 0
                type_table.add_row(
                    rt,
                    str(data["attempts"]),
                    str(data["retries"]),
                    f"{rate:.1%}",
                )

            console.print(type_table)

        # ── Failure Reasons ──
        if reasons:
            reason_table = Table(
                title="🔍 Failure Analysis",
                show_header=True,
                header_style="bold magenta",
            )
            reason_table.add_column("Reason / Category", style="bold")
            reason_table.add_column("Count", justify="right")

            for reason, count in sorted(
                reasons.items(), key=lambda x: x[1], reverse=True
            ):
                reason_table.add_row(reason, str(count))

            console.print(reason_table)

        # ── Alerts ──
        if alerts:
            console.print("")
            for alert in alerts:
                console.print(f"[bold red]{alert}[/bold red]")
        else:
            console.print("\n[green]✅ No retry alerts — all within threshold.[/green]")
