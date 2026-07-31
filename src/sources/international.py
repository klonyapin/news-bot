"""海外・英語ソースを RSS で取り込む。Yahoo とは並列に fetch。"""

from __future__ import annotations

import asyncio
import logging
from typing import TypedDict
from urllib.parse import urlparse, urlunparse

import feedparser
import httpx
from dateutil import parser as date_parser

from src.models import Category, NewsItem


logger = logging.getLogger(__name__)


class FeedConfig(TypedDict):
    name: str
    url: str
    category: Category
    max_items: int


# デフォルトで有効な海外・英語フィード。max_items で 1 フィードあたりの上限を絞る
# (Haiku のスコアリングコストと dedup 前候補プールのサイズを抑えるため)
INTERNATIONAL_FEEDS: list[FeedConfig] = [
    {"name": "BBC Japanese",  "url": "https://feeds.bbci.co.uk/japanese/rss.xml",           "category": "world",    "max_items": 15},
    {"name": "BBC World",     "url": "https://feeds.bbci.co.uk/news/world/rss.xml",         "category": "world",    "max_items": 10},
    {"name": "BBC Business",  "url": "https://feeds.bbci.co.uk/news/business/rss.xml",      "category": "business", "max_items": 10},
    {"name": "Al Jazeera",    "url": "https://www.aljazeera.com/xml/rss/all.xml",           "category": "world",    "max_items": 10},
    {"name": "Reuters",       "url": "https://news.google.com/rss/search?q=site:reuters.com&hl=en-US&gl=US&ceid=US:en",     "category": "world",    "max_items": 15},
    {"name": "Bloomberg",     "url": "https://news.google.com/rss/search?q=site:bloomberg.com&hl=en-US&gl=US&ceid=US:en",   "category": "business", "max_items": 15},
]

USER_AGENT = "Mozilla/5.0 (compatible; news-bot/0.1; +https://github.com/klonyapin/news-bot)"


def _normalize_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse(parsed._replace(query="", fragment=""))


async def fetch_feed(client: httpx.AsyncClient, cfg: FeedConfig) -> list[NewsItem]:
    try:
        response = await client.get(cfg["url"], headers={"User-Agent": USER_AGENT}, timeout=20.0)
        response.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning("International feed fetch failed for %s: %s", cfg["name"], e)
        return []

    parsed = feedparser.parse(response.text)
    items: list[NewsItem] = []
    for entry in parsed.entries[: cfg["max_items"]]:
        link = entry.get("link", "")
        if not link:
            continue
        try:
            published = date_parser.parse(entry.get("published", "")) if entry.get("published") else None
        except (ValueError, TypeError):
            published = None
        summary = str(entry.get("summary", "") or "").strip()
        # HTML タグを雑に落とす (BBC/AJ は description に HTML を含む)
        summary = _strip_html(summary)
        items.append(
            NewsItem(
                title=str(entry.title).strip(),
                url=_normalize_url(link),
                summary=summary[:400],
                category=cfg["category"],
                published_at=published,
                source=cfg["name"],
            )
        )
    logger.info("Fetched %d items from %s", len(items), cfg["name"])
    return items


async def fetch_all() -> list[NewsItem]:
    """全海外フィードを並列取得。個別失敗は log warning のみで残りは継続。"""
    async with httpx.AsyncClient(follow_redirects=True) as client:
        tasks = [fetch_feed(client, cfg) for cfg in INTERNATIONAL_FEEDS]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    items: list[NewsItem] = []
    for cfg, res in zip(INTERNATIONAL_FEEDS, results):
        if isinstance(res, Exception):
            logger.warning("International source %s raised: %s", cfg["name"], res)
            continue
        items.extend(res)
    logger.info("International total: %d items across %d feeds", len(items), len(INTERNATIONAL_FEEDS))
    return items


import re

_HTML_TAG = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    return _HTML_TAG.sub("", s).strip()
