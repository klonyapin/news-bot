"""重要度フィルタ + story 単位の重複除去。Haiku で全記事を一括採点する。"""

from __future__ import annotations

import json
import logging
import os
import re

from anthropic import AsyncAnthropic

from src.models import NewsItem


logger = logging.getLogger(__name__)


IMPORTANCE_MODEL = "claude-haiku-4-5"


SYSTEM_PROMPT = """あなたはニュース編集長です。以下の記事を採点し、同じ出来事の別記事をグルーピングしてください。

**採点基準 (0-10):**
- 9-10: 一大事。金融市場・国政・国際情勢を大きく動かす。歴史的節目。
- 7-8:  重要。政策決定、要人発言、企業M&A、大災害、外交合意など多くの人に直接影響。
- 5-6:  中程度。特定分野では重要だが影響範囲は限定。専門家向けの一報。
- 3-4:  通常のニュース。記録的価値はあるがトピックとして目新しくない。
- 0-2:  業界内・地域内のニュース、話題性のみ、芸能・スポーツ雑報。

**story_id (最重要):**
同じ「出来事」の記事は同じ story_id を付けてください。
- 例1: 「熊本地震死者36人」「被災自治体に交付税前倒し」「新幹線再開困難」→ すべて `kumamoto_quake_2026`
- 例2: 「日銀 政策金利据え置き」「日銀総裁 上振れリスク意識」→ `boj_meeting_202607`
- 例3: 「ハマス武装解除合意」「米大統領イラン打撃発言」→ 別 story なので別 id
- スラグは小文字英数 + アンダースコア、20 文字以内
- 過去に流布した同じ話題には出来る限り同じ id を使う (例えば熊本地震は `kumamoto_quake_2026` に統一)

**厳密に以下の JSON スキーマで返答すること (前後に文章を付けない):**

[{"index": 0, "importance": 8, "story_id": "kumamoto_quake_2026", "reason": "..."}, ...]

- index は 0 始まり、入力順に対応
- 全記事に必ずスコアを付ける
- reason は 20 文字以内で簡潔に
"""


_SLUG_ALLOWED = re.compile(r"[^a-z0-9_]")


def _normalize_story_id(raw: str) -> str:
    if not raw:
        return ""
    s = raw.strip().lower().replace(" ", "_").replace("-", "_")
    s = _SLUG_ALLOWED.sub("", s)
    return s[:40]


class ImportanceScorer:
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.client = AsyncAnthropic(api_key=api_key)
        self.model = model or IMPORTANCE_MODEL

    async def score(self, items: list[NewsItem]) -> list[NewsItem]:
        """全記事に importance と story_id を付けて返す。API 失敗時は heat 順の疑似スコアを付ける。"""
        if not items:
            return items

        headlines = "\n".join(
            f"[{i}] ({item.category}) {item.title} | 要約: {item.summary[:100] or '(なし)'}"
            for i, item in enumerate(items)
        )
        user_message = f"以下の記事を採点し、同じ出来事の記事に同じ story_id を付けてください。\n\n{headlines}"

        try:
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
        except Exception as e:
            logger.warning("Importance scoring failed, falling back to heat score: %s", e)
            return _fallback_by_heat(items)

        text = "".join(b.text for b in response.content if b.type == "text").strip()
        text = _strip_fence(text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse importance JSON, falling back: %s", e)
            return _fallback_by_heat(items)

        entries: dict[int, tuple[int, str]] = {}
        for entry in data:
            try:
                idx = int(entry["index"])
                score = max(0, min(10, int(entry["importance"])))
                story_id = _normalize_story_id(str(entry.get("story_id", "")))
                entries[idx] = (score, story_id)
            except (KeyError, ValueError, TypeError):
                continue

        result: list[NewsItem] = []
        for i, item in enumerate(items):
            score, story_id = entries.get(i, (0, ""))
            result.append(item.model_copy(update={"importance": score, "story_id": story_id}))

        story_count = len({i.story_id for i in result if i.story_id})
        logger.info(
            "Scored %d items (max=%d, avg=%.1f, distinct stories=%d)",
            len(result),
            max((i.importance for i in result), default=0),
            sum(i.importance for i in result) / max(len(result), 1),
            story_count,
        )
        return result


def _fallback_by_heat(items: list[NewsItem]) -> list[NewsItem]:
    """API 失敗時: heat_score を 0-10 にスケーリングして importance にする。story_id は空。"""
    if not items:
        return items
    max_heat = max((i.heat_score for i in items), default=1.0) or 1.0
    return [
        item.model_copy(update={"importance": min(10, int(item.heat_score / max_heat * 10))})
        for item in items
    ]


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def filter_important(
    items: list[NewsItem],
    threshold: int,
    max_items: int,
    exclude_story_ids: set[str] | None = None,
) -> list[NewsItem]:
    """閾値以上を importance 降順で最大 max_items 件返す。

    dedup ルール:
    - `exclude_story_ids` に含まれる story_id を持つ記事は除外 (cross-run dedup)
    - 残った候補の中で、同一 story_id は最高 importance の 1 件だけ残す (within-run dedup)
    """
    excluded = exclude_story_ids or set()
    candidates = [
        i for i in items
        if i.importance >= threshold and (not i.story_id or i.story_id not in excluded)
    ]
    candidates.sort(key=lambda x: (x.importance, x.heat_score), reverse=True)

    seen_stories: set[str] = set()
    deduped: list[NewsItem] = []
    for item in candidates:
        if item.story_id and item.story_id in seen_stories:
            continue
        if item.story_id:
            seen_stories.add(item.story_id)
        deduped.append(item)
        if len(deduped) >= max_items:
            break
    return deduped
