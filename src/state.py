"""投稿済み URL の状態管理。GitHub Actions cache で run 間永続化される想定。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path


logger = logging.getLogger(__name__)


DEFAULT_STATE_PATH = Path("state/posted.json")
DEFAULT_TTL_HOURS = 72


class PostedState:
    """URL → 投稿タイムスタンプの辞書。TTL 超過分は自動削除。"""

    def __init__(self, path: Path = DEFAULT_STATE_PATH, ttl_hours: int = DEFAULT_TTL_HOURS) -> None:
        self.path = path
        self.ttl = timedelta(hours=ttl_hours)
        self._data: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            logger.info("No existing state at %s, starting fresh", self.path)
            return
        try:
            with self.path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load state (%s), starting fresh", e)
            return

        now = datetime.now(timezone.utc)
        for url, ts_str in raw.items():
            try:
                ts = datetime.fromisoformat(ts_str)
                if now - ts <= self.ttl:
                    self._data[url] = ts_str
            except ValueError:
                continue
        logger.info("Loaded %d posted URLs (dropped %d stale)", len(self._data), len(raw) - len(self._data))

    def is_posted(self, url: str) -> bool:
        return url in self._data

    def mark_posted(self, url: str) -> None:
        self._data[url] = datetime.now(timezone.utc).isoformat()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2, sort_keys=True)
        tmp.replace(self.path)
        logger.info("Saved %d posted URLs to %s", len(self._data), self.path)
