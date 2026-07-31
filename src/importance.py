"""重要度フィルタ。Haiku で全記事を一括スコアリングして閾値以上のみ返す。"""

from __future__ import annotations

import json
import logging
import os

from anthropic import AsyncAnthropic

from src.models import NewsItem


logger = logging.getLogger(__name__)


IMPORTANCE_MODEL = "claude-haiku-4-5"


SYSTEM_PROMPT = """あなたはニュース編集長です。以下の記事を「速報として今すぐ Discord に流す価値があるか」で採点してください。

採点基準 (0-10):
- 9-10: 一大事。金融市場・国政・国際情勢を大きく動かす。歴史的節目。
- 7-8: 重要。政策決定、要人発言、企業M&A、大災害、外交合意など多くの人に直接影響。
- 5-6: 中程度。特定分野では重要だが影響範囲は限定。専門家向けの一報。
- 3-4: 通常のニュース。記録的価値はあるがトピックとして目新しくない。
- 0-2: 業界内・地域内のニュース、話題性のみ、芸能・スポーツ雑報。

厳密に以下の JSON スキーマで返答すること (前後に文章を付けない):

[{"index": 0, "importance": 8, "reason": "..."}, ...]

- index は 0 始まり、入力順に対応
- 全記事に必ずスコアを付ける
- reason は 20 文字以内で簡潔に
"""


class ImportanceScorer:
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.client = AsyncAnthropic(api_key=api_key)
        self.model = model or IMPORTANCE_MODEL

    async def score(self, items: list[NewsItem]) -> list[NewsItem]:
        """全記事に importance を付けて返す。API 失敗時はヒート順の疑似スコアを付ける。"""
        if not items:
            return items

        headlines = "\n".join(
            f"[{i}] ({item.category}) {item.title} | 要約: {item.summary[:100] or '(なし)'}"
            for i, item in enumerate(items)
        )
        user_message = f"以下の記事を採点してください。\n\n{headlines}"

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

        scores: dict[int, int] = {}
        for entry in data:
            try:
                idx = int(entry["index"])
                score = max(0, min(10, int(entry["importance"])))
                scores[idx] = score
            except (KeyError, ValueError, TypeError):
                continue

        result: list[NewsItem] = []
        for i, item in enumerate(items):
            score = scores.get(i, 0)
            result.append(item.model_copy(update={"importance": score}))
        logger.info("Scored %d items (max=%d, avg=%.1f)",
                    len(result),
                    max((i.importance for i in result), default=0),
                    sum(i.importance for i in result) / max(len(result), 1))
        return result


def _fallback_by_heat(items: list[NewsItem]) -> list[NewsItem]:
    """API 失敗時: heat_score を 0-10 にスケーリングして importance にする。"""
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


def filter_important(items: list[NewsItem], threshold: int, max_items: int) -> list[NewsItem]:
    """閾値以上を importance 降順で最大 max_items 件返す。"""
    important = [i for i in items if i.importance >= threshold]
    important.sort(key=lambda x: (x.importance, x.heat_score), reverse=True)
    return important[:max_items]
