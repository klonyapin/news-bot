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

# Discord limits
MAX_CONTENT = 2000
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
        content = f"━━━━━━━━━━━━━━━━━━━━━━\n### 🗞️ {now} — 速報 {count} 本\n━━━━━━━━━━━━━━━━━━━━━━"
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
            # エラー時は content だけ簡潔に
            content = (
                f"{badge}  ·  {category_label}\n"
                f"## [{_truncate(item.title, 200)}](<{item.url}>)\n"
                f"⚠️ 分析エラー: {_truncate(analysis.error, 800)}"
            )
            await self._post(client, {"content": _truncate(content, MAX_CONTENT)})
            return

        content = _build_content(item, analysis, badge, category_label)
        embed = _build_analysis_embed(item, analysis, color)

        payload = {"content": content}
        if embed and (embed.get("fields") or embed.get("description")):
            payload["embeds"] = [embed]

        await self._post(client, payload)

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


def _build_content(
    item: NewsItem,
    analysis: AnalysisResult,
    badge: str,
    category_label: str,
) -> str:
    """本文 (見出し + 要約): Discord のメイン領域に大きく表示される部分。"""
    lines: list[str] = []

    lines.append(f"{badge}  ·  {category_label}")
    lines.append(f"## [{_truncate(item.title, 200)}](<{item.url}>)")

    signals = []
    if item.ranking_position:
        signals.append(f"📊 ランキング #{item.ranking_position}")
    if item.trending_keywords:
        signals.append(f"🔥 {' / '.join(item.trending_keywords[:3])}")
    if signals:
        lines.append("-# " + "  ·  ".join(signals))

    summary_ja = analysis.summary.ja or item.summary
    if summary_ja:
        lines.append("")
        lines.append(f"**📌 要約**")
        lines.append(summary_ja)

    if analysis.summary.en:
        lines.append("")
        lines.append(f"**Summary (English)**")
        lines.append(analysis.summary.en)

    text = "\n".join(lines)
    return _truncate(text, MAX_CONTENT)


def _build_analysis_embed(item: NewsItem, analysis: AnalysisResult, color: int) -> dict:
    """4 軸解説 + 情報源を Embed (コンパクト表示) に。"""
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

    if not fields:
        return {}

    embed = {
        "color": color,
        "fields": fields,
        "footer": {"text": f"Yahoo! ニュース  ·  重要度 {item.importance}/10"},
    }
    if item.published_at:
        embed["timestamp"] = item.published_at.isoformat()

    _ensure_within_limit(embed)
    return embed


def _format_bilingual(bilingual: BilingualText) -> str:
    """1 フィールド内に JA + EN 併記。embed 内は subtext markdown が効かないので italic + separator。"""
    if not bilingual.ja and not bilingual.en:
        return ""
    if not bilingual.en:
        return _truncate(bilingual.ja, MAX_FIELD_VALUE)
    ja_budget = MAX_FIELD_VALUE // 2 - 8
    en_budget = MAX_FIELD_VALUE - ja_budget - 20
    ja = _truncate(bilingual.ja, ja_budget)
    en = _truncate(bilingual.en, en_budget)
    return f"{ja}\n\n*{en}*"


def _truncate(text: str, limit: int) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _ensure_within_limit(embed: dict) -> None:
    while _embed_char_count(embed) > MAX_EMBED_TOTAL and embed.get("fields"):
        embed["fields"].pop()


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
