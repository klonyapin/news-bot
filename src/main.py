from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

import httpx

from src.analyzer import Analyzer
from src.discord_client import DiscordClient
from src.importance import ImportanceScorer, filter_important
from src.models import NewsItem
from src.sources import yahoo_news, yahoo_realtime
from src.state import PostedState


def setup_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


logger = logging.getLogger(__name__)


async def collect_candidates() -> list[NewsItem]:
    """全ソースから記事とトレンドを並列取得。"""
    trending_task = asyncio.create_task(yahoo_realtime.fetch_trending_keywords())
    news_task = asyncio.create_task(yahoo_news.fetch_all())
    trending = await trending_task
    items = await news_task
    items = yahoo_news.apply_trending(items, trending)
    logger.info("Collected %d candidate items", len(items))
    return items


async def run(
    importance_threshold: int,
    max_items: int,
    dry_run: bool,
    skip_dedupe: bool,
) -> None:
    state = PostedState()
    items = await collect_candidates()

    if not skip_dedupe:
        items = [i for i in items if not state.is_posted(str(i.url))]
        logger.info("%d items remain after dedupe", len(items))

    if not items:
        logger.info("No new items. Exiting.")
        return

    scorer = ImportanceScorer()
    scored = await scorer.score(items)

    recent_stories = state.recent_story_ids()
    if recent_stories:
        logger.info("Excluding %d recently-posted stories", len(recent_stories))

    important = filter_important(
        scored,
        threshold=importance_threshold,
        max_items=max_items,
        exclude_story_ids=recent_stories,
    )
    logger.info(
        "After importance+dedup filter (threshold=%d): %d/%d items",
        importance_threshold, len(important), len(scored),
    )

    if not important:
        logger.info("No items passed importance threshold. Exiting.")
        return

    for i, item in enumerate(important, 1):
        logger.info(
            "  [%d] importance=%d cat=%s | %s",
            i, item.importance, item.category, item.title[:60],
        )

    analyzer = Analyzer()

    if dry_run:
        analyses = await asyncio.gather(*(analyzer.analyze(i) for i in important))
        for item, analysis in zip(important, analyses):
            print("=" * 80)
            print(f"[{item.importance}/10] {item.title}\n{item.url}\n")
            if analysis.error:
                print(f"ERROR: {analysis.error}")
                continue
            for label, bilingual in [
                ("要約", analysis.summary),
                ("裏付け", analysis.evidence),
                ("背景", analysis.background),
                ("注意点", analysis.caveats),
                ("今後の動向", analysis.outlook),
            ]:
                print(f"■ {label}\n{bilingual.ja}\n[EN] {bilingual.en}\n")
            for src in analysis.sources:
                print(f"  - {src.title} | {src.url}")
        return

    webhook_url = os.environ["DISCORD_WEBHOOK_URL"]
    discord = DiscordClient(webhook_url)

    async def analyze_pair(item: NewsItem):
        return item, await analyzer.analyze(item)

    async with httpx.AsyncClient() as client:
        await discord.post_run_header(client, important)

        tasks = [asyncio.create_task(analyze_pair(item)) for item in important]
        posted_count = 0
        for coro in asyncio.as_completed(tasks):
            item, analysis = await coro
            try:
                await discord.post_article(client, item, analysis)
                if not analysis.error:
                    state.mark_posted(str(item.url), story_id=item.story_id)
                    posted_count += 1
            except httpx.HTTPError as e:
                logger.error("Failed to post %s: %s", item.title[:40], e)
            await asyncio.sleep(0.5)

    state.save()
    logger.info("Posted %d/%d articles", posted_count, len(important))


def main() -> None:
    parser = argparse.ArgumentParser(description="Yahoo! ニュース速報 Discord Bot")
    parser.add_argument(
        "--threshold",
        type=int,
        default=int(os.getenv("IMPORTANCE_THRESHOLD", "7")),
        help="重要度スコアの投稿閾値 (0-10, デフォルト 7)",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=int(os.getenv("MAX_ITEMS_PER_RUN", "5")),
        help="1 回の実行での最大投稿数 (デフォルト 5)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Discord に投稿せず標準出力に出す",
    )
    parser.add_argument(
        "--skip-dedupe",
        action="store_true",
        help="state/posted.json をチェックしない (テスト用)",
    )
    args = parser.parse_args()

    setup_logging()

    if not args.dry_run and not os.getenv("DISCORD_WEBHOOK_URL"):
        logger.error("DISCORD_WEBHOOK_URL is not set")
        sys.exit(2)
    if not os.getenv("ANTHROPIC_API_KEY"):
        logger.error("ANTHROPIC_API_KEY is not set")
        sys.exit(2)

    asyncio.run(
        run(
            importance_threshold=args.threshold,
            max_items=args.max_items,
            dry_run=args.dry_run,
            skip_dedupe=args.skip_dedupe,
        )
    )


if __name__ == "__main__":
    main()
