from __future__ import annotations

import json
import logging
import re

import httpx
from bs4 import BeautifulSoup


logger = logging.getLogger(__name__)


REALTIME_URL = "https://search.yahoo.co.jp/realtime"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


async def fetch_trending_keywords(limit: int = 20) -> list[str]:
    """リアルタイム検索のトップ画面からトレンドキーワードを取得。

    Yahoo! リアルタイム検索は SPA で JS レンダリングが必要な場合がある。
    初期 HTML に `__NEXT_DATA__` または類似の hydration データがあれば
    そこから抽出。無ければ空リストを返して pipeline を継続する。
    """
    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            response = await client.get(
                REALTIME_URL,
                headers={"User-Agent": USER_AGENT, "Accept-Language": "ja,en;q=0.9"},
                timeout=15.0,
            )
            response.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("Realtime search fetch failed: %s", e)
            return []

    keywords = _extract_from_next_data(response.text)
    if not keywords:
        keywords = _extract_from_html(response.text)

    keywords = [k for k in keywords if _is_meaningful_keyword(k)]

    logger.info("Extracted %d trending keywords from realtime search", len(keywords))
    return keywords[:limit]


def _extract_from_next_data(html: str) -> list[str]:
    """<script id="__NEXT_DATA__"> の JSON からキーワードを抽出。"""
    soup = BeautifulSoup(html, "lxml")
    script = soup.find("script", id="__NEXT_DATA__")
    if script is None or not script.string:
        return []
    try:
        data = json.loads(script.string)
    except json.JSONDecodeError:
        return []

    keywords: list[str] = []
    _walk_for_keywords(data, keywords)
    return keywords


def _walk_for_keywords(obj, out: list[str], depth: int = 0) -> None:
    """再帰的に dict を歩いて "keyword"/"word"/"query" フィールドを収集。"""
    if depth > 8:
        return
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in ("keyword", "word", "query", "trendKeyword") and isinstance(value, str):
                out.append(value)
            else:
                _walk_for_keywords(value, out, depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            _walk_for_keywords(item, out, depth + 1)


def _extract_from_html(html: str) -> list[str]:
    """HTML から fallback で class 名を頼りにキーワードを拾う。"""
    soup = BeautifulSoup(html, "lxml")
    keywords: list[str] = []

    for element in soup.select("a[href*='/realtime/search']"):
        text = element.get_text(strip=True)
        if text:
            keywords.append(text)

    return keywords


_MEANINGLESS_PATTERNS = re.compile(r"^(次へ|前へ|検索|もっと見る|\d+)$")


def _is_meaningful_keyword(keyword: str) -> bool:
    if not keyword or len(keyword) < 2 or len(keyword) > 30:
        return False
    if _MEANINGLESS_PATTERNS.match(keyword):
        return False
    return True
