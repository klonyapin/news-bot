"""投稿済み URL の状態管理。GitHub Actions cache で run 間永続化される想定。

各エントリは {"posted_at": iso8601, "story_id": str} を保持。
古いフォーマット (URL → iso8601 の flat string) からも自動移行する。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path


logger = logging.getLogger(__name__)


DEFAULT_STATE_PATH = Path("state/posted.json")
DEFAULT_TTL_HOURS = 72


class PostedState:
    def __init__(self, path: Path = DEFAULT_STATE_PATH, ttl_hours: int = DEFAULT_TTL_HOURS) -> None:
        self.path = path
        self.ttl = timedelta(hours=ttl_hours)
        # URL -> {"posted_at": iso, "story_id": str}
        self._data: dict[str, dict] = {}
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
        for url, val in raw.items():
            entry = _normalize_entry(val)
            if entry is None:
                continue
            try:
                ts = datetime.fromisoformat(entry["posted_at"])
            except (ValueError, KeyError):
                continue
            if now - ts <= self.ttl:
                self._data[url] = entry
        logger.info("Loaded %d posted URLs (dropped %d stale/invalid)", len(self._data), len(raw) - len(self._data))

    def is_posted(self, url: str) -> bool:
        return url in self._data

    def recent_story_ids(self) -> set[str]:
        """TTL 内に投稿した story_id 一覧 (cross-run dedup 用)。"""
        return {v["story_id"] for v in self._data.values() if v.get("story_id")}

    def mark_posted(self, url: str, story_id: str = "") -> None:
        # 既存 URL に story_id が付いていて、新 story_id が空なら既存を保持する
        existing = self._data.get(url, {})
        self._data[url] = {
            "posted_at": datetime.now(timezone.utc).isoformat(),
            "story_id": story_id or existing.get("story_id", ""),
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2, sort_keys=True)
        tmp.replace(self.path)
        story_count = len({v.get("story_id") for v in self._data.values() if v.get("story_id")})
        logger.info(
            "Saved %d posted URLs / %d distinct stories to %s",
            len(self._data), story_count, self.path,
        )


def _normalize_entry(val) -> dict | None:
    """旧フォーマット (URL → iso string) と新フォーマット (dict) の両方を受ける。"""
    if isinstance(val, str):
        return {"posted_at": val, "story_id": ""}
    if isinstance(val, dict):
        ts = val.get("posted_at")
        if not isinstance(ts, str):
            return None
        return {"posted_at": ts, "story_id": str(val.get("story_id", ""))}
    return None
