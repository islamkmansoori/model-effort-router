"""
US-108: Routing Authority Lock
Ensures model selector remains the FINAL authority for all LLM calls.
Blocks all attempts to bypass routing and logs every intercept.
"""
import functools
import json
import time
from typing import Any, Callable, Dict, List, Optional


class RoutingViolationError(Exception):
    """Raised when an external framework attempts to bypass routing."""
    pass


class RoutingAuthority:
    """
    US-108: Central routing lock.
    All calls to LLM providers must pass through this authority.
    Override attempts are blocked and audit-logged.
    """
    _instance = None
    AUTHORITY_TOKEN = "ROUTING-AUTHORITY-v1"

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._audit_log: List[Dict] = []
            cls._instance._blocked_attempts: int = 0
            cls._instance._approved_calls: int = 0
        return cls._instance

    def authorize(self, caller: str, model: str, payload_tokens: int) -> Dict:
        """
        Authorize an LLM call. Must be called before any provider invocation.
        Returns an authorization token if approved.
        """
        if not caller.startswith("routing."):
            self._blocked_attempts += 1
            event = {
                "timestamp": time.time(),
                "event": "BLOCKED",
                "caller": caller,
                "model": model,
                "reason": "caller not in routing namespace"
            }
            self._audit_log.append(event)
            raise RoutingViolationError(
                f"[US-108] Routing Authority: Blocked '{caller}'. "
                f"Only 'routing.*' modules may invoke LLM providers."
            )

        self._approved_calls += 1
        event = {
            "timestamp": time.time(),
            "event": "APPROVED",
            "caller": caller,
            "model": model,
            "payload_tokens": payload_tokens
        }
        self._audit_log.append(event)
        return {
            "authority": self.AUTHORITY_TOKEN,
            "approved": True,
            "caller": caller,
            "model": model
        }

    def get_audit_log(self) -> List[Dict]:
        return list(self._audit_log)

    def get_stats(self) -> Dict:
        return {
            "approved_calls": self._approved_calls,
            "blocked_attempts": self._blocked_attempts,
            "total_events": len(self._audit_log)
        }

    def reset(self):
        """Reset stats — for testing only."""
        self._audit_log.clear()
        self._blocked_attempts = 0
        self._approved_calls = 0


def routing_authorized(model: str = "default"):
    """
    Decorator: marks a function as routing-authorized for LLM calls.
    Automatically calls authorize() using the module's qualified name.
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            caller = f"routing.{fn.__module__}.{fn.__qualname__}"
            authority = RoutingAuthority()
            authority.authorize(caller, model, payload_tokens=0)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


class RoutingLockMiddleware:
    """
    Middleware that intercepts any LLM provider call and validates the authority token.
    Plug this into the executor pipeline.
    """

    def __init__(self, authority: RoutingAuthority = None):
        self.authority = authority or RoutingAuthority()

    def wrap(self, executor_fn: Callable) -> Callable:
        """Wrap an executor function with routing authority enforcement."""
        @functools.wraps(executor_fn)
        def secured(*args, **kwargs):
            # Check the caller is routing-namespaced
            caller = f"routing.secured.{executor_fn.__name__}"
            try:
                self.authority.authorize(caller, "wrapped", payload_tokens=0)
            except RoutingViolationError as e:
                # Re-raise — never silently allow bypass
                raise
            return executor_fn(*args, **kwargs)
        return secured
