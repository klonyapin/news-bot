from __future__ import annotations

import logging
from typing import Iterable
from urllib.parse import urlparse, urlunparse

import feedparser
import httpx
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from src.models import Category, NewsItem


logger = logging.getLogger(__name__)


RSS_URLS: dict[Category, str] = {
    "domestic": "https://news.yahoo.co.jp/rss/topics/domestic.xml",
    "world": "https://news.yahoo.co.jp/rss/topics/world.xml",
    "business": "https://news.yahoo.co.jp/rss/topics/business.xml",
}

RANKING_URLS: dict[Category, str] = {
    "domestic": "https://news.yahoo.co.jp/ranking/access/news/domestic",
    "world": "https://news.yahoo.co.jp/ranking/access/news/world",
    "business": "https://news.yahoo.co.jp/ranking/access/news/business",
}

USER_AGENT = "news-bot/0.1 (+https://github.com/klonyapin/news-bot)"


def _normalize_url(url: str) -> str:
    """クエリ・フラグメントを落として突合キーにする。"""
    parsed = urlparse(url)
    return urlunparse(parsed._replace(query="", fragment=""))


async def fetch_rss(client: httpx.AsyncClient, category: Category) -> list[NewsItem]:
    """RSS フィードから記事一覧を取得。"""
    url = RSS_URLS[category]
    try:
        response = await client.get(url, headers={"User-Agent": USER_AGENT}, timeout=15.0)
        response.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning("RSS fetch failed for %s: %s", category, e)
        return []

    parsed = feedparser.parse(response.text)
    items: list[NewsItem] = []
    for entry in parsed.entries:
        try:
            published = date_parser.parse(entry.get("published", "")) if entry.get("published") else None
        except (ValueError, TypeError):
            published = None
        items.append(
            NewsItem(
                title=entry.title,
                url=_normalize_url(entry.link),
                summary=entry.get("summary", ""),
                category=category,
                published_at=published,
                source="Yahoo!",
            )
        )
    logger.info("Fetched %d items from %s RSS", len(items), category)
    return items


async def fetch_ranking(client: httpx.AsyncClient, category: Category) -> dict[str, int]:
    """アクセスランキングから URL → 順位のマップを作る。

    HTML 構造が変わる可能性が高いので、best-effort。失敗時は空辞書を返す。
    """
    url = RANKING_URLS[category]
    try:
        response = await client.get(url, headers={"User-Agent": USER_AGENT}, timeout=15.0)
        response.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning("Ranking fetch failed for %s: %s", category, e)
        return {}

    soup = BeautifulSoup(response.text, "lxml")
    ranking: dict[str, int] = {}

    for position, link in enumerate(soup.select("a[href*='/pickup/']"), start=1):
        href = link.get("href", "")
        if href and href.startswith("https://news.yahoo.co.jp/pickup/"):
            ranking.setdefault(_normalize_url(href), position)

    logger.info("Parsed %d ranked URLs from %s ranking", len(ranking), category)
    return ranking


def apply_ranking(items: Iterable[NewsItem], ranking: dict[str, int]) -> list[NewsItem]:
    """記事に順位情報を注入。"""
    result: list[NewsItem] = []
    for item in items:
        url_str = str(item.url)
        if url_str in ranking:
            item = item.model_copy(update={"ranking_position": ranking[url_str]})
        result.append(item)
    return result


def apply_trending(items: Iterable[NewsItem], keywords: list[str]) -> list[NewsItem]:
    """記事タイトル/要約にトレンドキーワードが含まれれば注入。"""
    result: list[NewsItem] = []
    for item in items:
        matched = [k for k in keywords if k in item.title or k in item.summary]
        if matched:
            item = item.model_copy(update={"trending_keywords": matched})
        result.append(item)
    return result


def deduplicate(items: Iterable[NewsItem]) -> list[NewsItem]:
    """URL で重複除去 (RSS と ranking で被る)。"""
    seen: dict[str, NewsItem] = {}
    for item in items:
        url = str(item.url)
        if url not in seen:
            seen[url] = item
    return list(seen.values())


CATEGORIES: tuple[Category, ...] = ("domestic", "world", "business")


async def fetch_all(
    trending_keywords: list[str] | None = None,
) -> list[NewsItem]:
    """国内・国際・経済の記事を全カテゴリ並列で取得し、順位とトレンドを付けて返す。"""
    import asyncio

    async with httpx.AsyncClient(follow_redirects=True) as client:
        rss_tasks = [fetch_rss(client, cat) for cat in CATEGORIES]
        rank_tasks = [fetch_ranking(client, cat) for cat in CATEGORIES]
        rss_results, rank_results = await asyncio.gather(
            asyncio.gather(*rss_tasks),
            asyncio.gather(*rank_tasks),
        )

    all_items: list[NewsItem] = [item for sub in rss_results for item in sub]
    ranking: dict[str, int] = {}
    for rank in rank_results:
        ranking.update(rank)

    items = deduplicate(all_items)
    items = apply_ranking(items, ranking)
    if trending_keywords:
        items = apply_trending(items, trending_keywords)

    return items
