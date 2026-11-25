"""
Prompt Cache for command line.
Caches repeated prompt contexts (system prompts, project structure)
to avoid re-sending them every request.

Implements: US-050 (Prompt Caching & Context Pruning)
"""
import hashlib
import json
import time
from pathlib import Path
from typing import Optional, Dict, Any, List
from .safe_io import safe_write_text
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


class PromptCache:
    """
    Manages prompt caching to reduce redundant context token usage.

    Features:
    - Content-based SHA-256 cache keys
    - TTL-based expiration (default 24h)
    - LRU eviction when max_size exceeded
    - Hit rate tracking
    - File-change invalidation
    """

    def __init__(
        self,
        cache_dir: str = ".routing_data/prompt_cache",
        ttl: int = 86400,
        max_size: int = 100,
    ):
        """
        Initialize prompt cache.

        Args:
            cache_dir: Directory for persistent cache files
            ttl: Time to live in seconds (default 24 hours)
            max_size: Maximum number of cache entries
        """
        self.cache_dir = Path(cache_dir)
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError):
            pass
        self.ttl = ttl
        self.max_size = max_size

        # In-memory index (fast lookups)
        self._index: Dict[str, Dict[str, Any]] = {}
        self.hits = 0
        self.misses = 0

        # Load existing index from disk
        self._load_index()

    # ── Cache Key Generation ──────────────────────────────────────────────

    def generate_cache_key(
        self,
        project_id: str,
        files: Optional[List[Dict[str, str]]] = None,
        context_version: str = "v1",
    ) -> str:
        """
        Generate deterministic cache key based on project context.

        Args:
            project_id: project identifier (e.g. repo name)
            files: List of {path, hash} dicts for relevant files
            context_version: Version of context format

        Returns:
            SHA-256 hex digest as cache key
        """
        key_components = [project_id, context_version]
        if files:
            key_components.extend(
                sorted(f"{f['path']}:{f['hash']}" for f in files)
            )

        key_string = "|".join(key_components)
        return hashlib.sha256(key_string.encode()).hexdigest()

    def generate_key_from_content(self, content: str) -> str:
        """Simple cache key from raw content string."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    # ── Cache Operations ──────────────────────────────────────────────────

    def get(self, cache_key: str) -> Optional[str]:
        """
        Retrieve cached prompt context.

        Args:
            cache_key: Cache key to lookup

        Returns:
            Cached context string if valid, None otherwise
        """
        if cache_key not in self._index:
            self.misses += 1
            return None

        entry = self._index[cache_key]

        # Check if expired
        if time.time() - entry["timestamp"] > self.ttl:
            self._evict(cache_key)
            self.misses += 1
            return None

        # Read content from disk
        cache_file = self.cache_dir / f"{cache_key}.txt"
        if not cache_file.exists():
            self._evict(cache_key)
            self.misses += 1
            return None

        # Update access time
        entry["last_access"] = time.time()
        entry["access_count"] = entry.get("access_count", 0) + 1
        self._save_index()
        self.hits += 1

        return cache_file.read_text()

    def set(self, cache_key: str, context: str) -> None:
        """
        Store prompt context in cache.

        Args:
            cache_key: Cache key
            context: Prompt context string to cache
        """
        # Enforce max size (LRU eviction)
        while len(self._index) >= self.max_size:
            self._evict_lru()

        # Write content to disk
        cache_file = self.cache_dir / f"{cache_key}.txt"
        safe_write_text(cache_file, context)

        # Update in-memory index
        self._index[cache_key] = {
            "timestamp": time.time(),
            "last_access": time.time(),
            "access_count": 1,
            "size_bytes": len(context.encode("utf-8")),
        }
        self._save_index()

    def invalidate(self, cache_key: str) -> bool:
        """
        Invalidate a specific cache entry.

        Returns:
            True if entry was removed, False if not found
        """
        return self._evict(cache_key)

    def invalidate_all(self) -> int:
        """Invalidate all cache entries. Returns count removed."""
        count = len(self._index)
        for key in list(self._index.keys()):
            self._evict(key)
        return count

    # ── Analytics ─────────────────────────────────────────────────────────

    def get_hit_rate(self) -> float:
        """Calculate cache hit rate."""
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        return {
            "size": len(self._index),
            "max_size": self.max_size,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": f"{self.get_hit_rate():.1%}",
            "total_bytes": sum(
                e.get("size_bytes", 0) for e in self._index.values()
            ),
            "ttl_hours": self.ttl / 3600,
        }

    # ── Internal Helpers ──────────────────────────────────────────────────

    def _evict(self, cache_key: str) -> bool:
        """Remove a single cache entry."""
        if cache_key not in self._index:
            return False
        del self._index[cache_key]
        cache_file = self.cache_dir / f"{cache_key}.txt"
        if cache_file.exists():
            cache_file.unlink()
        self._save_index()
        return True

    def _evict_lru(self) -> None:
        """Evict least recently used entry."""
        if not self._index:
            return
        lru_key = min(
            self._index.keys(),
            key=lambda k: self._index[k].get("last_access", 0),
        )
        self._evict(lru_key)

    def _load_index(self):
        """Load cache index from disk."""
        index_file = self.cache_dir / "_index.json"
        try:
            if index_file.exists():
                try:
                    self._index = json.loads(index_file.read_text())
                except (json.JSONDecodeError, Exception):
                    self._index = {}
        except (PermissionError, OSError):
            self._index = {}

    def _save_index(self):
        """Persist cache index to disk."""
        index_file = self.cache_dir / "_index.json"
        safe_write_text(index_file, json.dumps(self._index, indent=2))
