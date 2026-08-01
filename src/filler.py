"""チャンネルが静かになりすぎないように、Tier 2 news の谷間に投稿する軽い息抜き。

- 🐈 The Cat API から猫画像 1 枚
- 📰 Yahoo 話題 / livedoor エンタメ から軽い記事 1 件をランダム紹介

Tier 2 の裏事情解説とは別物なので、LLM 分析は無し。API コストほぼ 0。
"""

from __future__ import annotations

import logging
import os
import random
import sys
from datetime import datetime

import feedparser
import httpx


logger = logging.getLogger(__name__)


CAT_API = "https://api.thecatapi.com/v1/images/search?limit=1&mime_types=jpg,png,gif"

LIGHT_FEEDS: list[tuple[str, str]] = [
    ("Yahoo 話題",          "https://news.yahoo.co.jp/rss/categories/life.xml"),
    ("livedoor エンタメ",   "https://news.livedoor.com/topics/rss/ent.xml"),
    ("Yahoo エンタメ",      "https://news.yahoo.co.jp/rss/topics/entertainment.xml"),
    ("Yahoo IT",            "https://news.yahoo.co.jp/rss/topics/it.xml"),
]

USER_AGENT = "news-bot-filler/0.1 (+https://github.com/klonyapin/news-bot)"


def fetch_cat_image(client: httpx.Client) -> str | None:
    try:
        r = client.get(CAT_API, timeout=10.0)
        r.raise_for_status()
        data = r.json()
        if data and isinstance(data, list):
            return data[0].get("url")
    except Exception as e:
        logger.warning("Cat API failed: %s", e)
    return None


def fetch_light_news(client: httpx.Client) -> tuple[str, str, str] | None:
    """(source_name, title, url) を返す。全 feed 失敗なら None。"""
    feeds = random.sample(LIGHT_FEEDS, len(LIGHT_FEEDS))
    for name, url in feeds:
        try:
            r = client.get(url, headers={"User-Agent": USER_AGENT}, timeout=10.0)
            r.raise_for_status()
            parsed = feedparser.parse(r.text)
            if not parsed.entries:
                continue
            entry = random.choice(parsed.entries[:20])
            title = str(entry.title).strip()
            link = str(entry.link).strip()
            if title and link:
                return name, title, link
        except Exception as e:
            logger.warning("Feed %s failed: %s", name, e)
    return None


def post_to_discord(
    client: httpx.Client,
    webhook: str,
    cat_url: str | None,
    news: tuple[str, str, str] | None,
) -> None:
    now = datetime.now().strftime("%m/%d %H:%M JST")
    content = f"🐈 **今日の休憩**  ·  {now}"
    embed: dict = {"color": 0xFFA07A}

    if cat_url:
        embed["image"] = {"url": cat_url}

    if news:
        src, title, link = news
        embed["title"] = title[:250]
        embed["url"] = link
        embed["footer"] = {"text": src}
        embed["description"] = f"📰 息抜きに 1 本"

    if not cat_url and not news:
        # 両方失敗 = 投稿しない (空メッセージを送らない)
        logger.warning("Both cat and news fetch failed, skipping post")
        return

    payload = {"content": content, "embeds": [embed]}
    r = client.post(webhook, json=payload, timeout=15.0)
    r.raise_for_status()
    logger.info("Posted filler: cat=%s, news=%s", bool(cat_url), news[1][:40] if news else None)


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    webhook = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook:
        logger.error("DISCORD_WEBHOOK_URL not set")
        sys.exit(2)

    with httpx.Client(follow_redirects=True) as client:
        cat_url = fetch_cat_image(client)
        news = fetch_light_news(client)
        post_to_discord(client, webhook, cat_url, news)


if __name__ == "__main__":
    main()
