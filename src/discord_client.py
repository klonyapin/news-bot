from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import httpx

from src.models import AnalysisResult, BilingualText, NewsItem


logger = logging.getLogger(__name__)


CATEGORY_STYLE = {
    "domestic": (0x4A90E2, "🏛️ 国内・政治"),
    "world": (0xE67E22, "🌏 国際"),
    "business": (0x50C878, "💹 経済"),
}

# Discord Embed limits
MAX_DESCRIPTION = 4096
MAX_FIELD_VALUE = 1024
MAX_EMBED_TOTAL = 6000


def importance_badge(score: int) -> str:
    if score >= 9:
        return "🔴 最重要"
    if score >= 7:
        return "🟠 重要"
    if score >= 5:
        return "🟡 注目"
    return "⚪ 通常"


class DiscordClient:
    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url

    async def post_run_header(self, client: httpx.AsyncClient, count: int) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M JST")
        content = f"**[{now}] 速報 {count} 本**"
        await self._post(client, {"content": content})

    async def post_article(
        self,
        client: httpx.AsyncClient,
        item: NewsItem,
        analysis: AnalysisResult,
    ) -> None:
        color, category_label = CATEGORY_STYLE[item.category]
        badge = importance_badge(item.importance)

        if analysis.error:
            embed = {
                "title": _truncate(item.title, 250),
                "url": str(item.url),
                "description": f"⚠️ 分析エラー: {analysis.error}",
                "color": 0x808080,
            }
            await self._post(client, {"embeds": [embed]})
            return

        description = _build_description(item, analysis, badge, category_label)
        fields = _build_fields(analysis)

        embed = {
            "title": _truncate(item.title, 250),
            "url": str(item.url),
            "description": description,
            "color": color,
            "fields": fields,
            "footer": {"text": f"Yahoo! ニュース  |  重要度 {item.importance}/10"},
        }
        if item.published_at:
            embed["timestamp"] = item.published_at.isoformat()

        _ensure_within_limit(embed)
        await self._post(client, {"embeds": [embed]})

    async def _post(self, client: httpx.AsyncClient, payload: dict) -> None:
        for attempt in range(3):
            response = await client.post(self.webhook_url, json=payload, timeout=15.0)
            if response.status_code == 429:
                retry_after = float(response.headers.get("Retry-After", "1"))
                logger.warning("Discord rate limited, retry in %.1fs (attempt %d)", retry_after, attempt + 1)
                await asyncio.sleep(retry_after)
                continue
            response.raise_for_status()
            return
        raise RuntimeError("Discord post failed after retries")


def _build_description(
    item: NewsItem, analysis: AnalysisResult, badge: str, category_label: str
) -> str:
    header = f"{badge}  |  {category_label}"
    signals = []
    if item.ranking_position:
        signals.append(f"📊 ランキング #{item.ranking_position}")
    if item.trending_keywords:
        signals.append(f"🔥 {'/'.join(item.trending_keywords[:3])}")
    signals_line = "  |  ".join(signals)

    summary_ja = analysis.summary.ja or item.summary
    summary_en = analysis.summary.en

    parts = [header]
    if signals_line:
        parts.append(signals_line)
    parts.append(f"\n**📌 要約**\n{summary_ja}")
    if summary_en:
        parts.append(f"_{summary_en}_")

    text = "\n".join(parts)
    return _truncate(text, MAX_DESCRIPTION)


def _build_fields(analysis: AnalysisResult) -> list[dict]:
    fields: list[dict] = []
    for label, bilingual in [
        ("🔍 裏付け / Evidence", analysis.evidence),
        ("🌐 背景 / Background", analysis.background),
        ("⚠️ 注意点 / Caveats", analysis.caveats),
        ("🔮 今後の動向 / Outlook", analysis.outlook),
    ]:
        value = _format_bilingual(bilingual)
        if value:
            fields.append({"name": label, "value": value, "inline": False})

    if analysis.sources:
        lines = [f"• [{_truncate(s.title, 80)}]({s.url})" for s in analysis.sources[:3]]
        fields.append(
            {
                "name": "📚 情報源 / Sources",
                "value": _truncate("\n".join(lines), MAX_FIELD_VALUE),
                "inline": False,
            }
        )
    return fields


def _format_bilingual(bilingual: BilingualText) -> str:
    if not bilingual.ja and not bilingual.en:
        return ""
    if not bilingual.en:
        return _truncate(bilingual.ja, MAX_FIELD_VALUE)
    # Reserve budget: half for JA, half for EN (italic)
    ja_budget = MAX_FIELD_VALUE // 2 - 4
    en_budget = MAX_FIELD_VALUE - ja_budget - 8
    ja = _truncate(bilingual.ja, ja_budget)
    en = _truncate(bilingual.en, en_budget)
    return f"{ja}\n_{en}_"


def _truncate(text: str, limit: int) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _ensure_within_limit(embed: dict) -> None:
    """Embed 合計 6000 文字上限を超えていたら fields を末尾から削る。"""
    while _embed_char_count(embed) > MAX_EMBED_TOTAL and embed.get("fields"):
        embed["fields"].pop()
    if _embed_char_count(embed) > MAX_EMBED_TOTAL:
        embed["description"] = _truncate(
            embed.get("description", ""), MAX_EMBED_TOTAL - 200
        )


def _embed_char_count(embed: dict) -> int:
    total = 0
    total += len(embed.get("title", ""))
    total += len(embed.get("description", ""))
    for field in embed.get("fields", []):
        total += len(field.get("name", ""))
        total += len(field.get("value", ""))
    footer = embed.get("footer") or {}
    total += len(footer.get("text", ""))
    return total
