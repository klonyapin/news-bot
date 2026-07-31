from __future__ import annotations

import json
import logging
import os

from anthropic import AsyncAnthropic

from src.models import AnalysisResult, BilingualText, NewsItem, RelatedSource


logger = logging.getLogger(__name__)


DEFAULT_MODEL = "claude-sonnet-5"


SYSTEM_PROMPT = """あなたは日本の政治・国際・経済ニュースを鋭く読み解く報道アナリストです。

与えられた記事について、Web 検索で一次情報を確認したうえで、以下の 4 軸を日英併記で解説してください。

**軸の定義:**
1. summary (要約): 3-4 行で「何が起きたか」
2. evidence (裏付け): 記事の主張を支える一次ソース、公式発表、統計。誰が/いつ/どこで発表したかを明示
3. background (背景): なぜ今これが起きたか。関係者の思惑、業界慣習、政治的駆け引き。記事本文に書かれない文脈
4. caveats (注意点): 情報の不確実性、反対意見、誤読しやすい点、まだ判明していないこと
5. outlook (今後の動向): 数日〜数週間の見通し。追跡すべき次のイベント。市場や政策への波及可能性

**英訳のガイドライン:**
- ネイティブが読んで自然な英語
- 固有名詞は初出でカッコ内にローマ字併記 (例: 日銀 → the Bank of Japan (BoJ))
- 日本特有の概念は簡潔な補足を入れる

**厳密に以下の JSON スキーマで返答すること (前後に文章を付けない):**

{
  "summary":    {"ja": "...", "en": "..."},
  "evidence":   {"ja": "...", "en": "..."},
  "background": {"ja": "...", "en": "..."},
  "caveats":    {"ja": "...", "en": "..."},
  "outlook":    {"ja": "...", "en": "..."},
  "sources": [
    {"title": "...", "url": "https://..."},
    ...
  ]
}

sources には Web 検索で参照した情報源を最大 3 件。憶測になる部分は "〜とみられる" と明示。
"""


class Analyzer:
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.client = AsyncAnthropic(api_key=api_key)
        self.model = model or os.getenv("CLAUDE_MODEL", DEFAULT_MODEL)

    async def analyze(self, item: NewsItem) -> AnalysisResult:
        user_message = f"""以下の Yahoo! ニュース記事を解説してください。

タイトル: {item.title}
URL: {item.url}
カテゴリ: {item.category}
要約: {item.summary or "(RSS 概要なし)"}
話題度シグナル:
- 重要度スコア (自動判定): {item.importance}/10
- アクセスランキング順位: {item.ranking_position or "圏外"}
- リアルタイム検索で関連したキーワード: {", ".join(item.trending_keywords) or "なし"}

Web 検索で本文と一次情報を必ず確認し、指定された JSON スキーマで日英併記の解説を出力してください。"""

        try:
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=6144,
                system=SYSTEM_PROMPT,
                tools=[
                    {
                        "type": "web_search_20260209",
                        "name": "web_search",
                        "max_uses": 4,
                    }
                ],
                messages=[{"role": "user", "content": user_message}],
            )
        except Exception as e:
            logger.error("Claude API error for %s: %s", item.title[:40], e)
            return AnalysisResult(
                summary=BilingualText(ja=item.summary or item.title),
                error=f"分析エラー: {type(e).__name__}: {e}",
            )

        return self._parse_response(response, item)

    def _parse_response(self, response, item: NewsItem) -> AnalysisResult:
        text_blocks = [block.text for block in response.content if block.type == "text"]
        if not text_blocks:
            logger.warning("No text block in response for %s", item.title[:40])
            return AnalysisResult(
                summary=BilingualText(ja=item.summary or item.title),
                error="Claude からの応答が空でした",
            )

        raw = "".join(text_blocks).strip()
        raw = _strip_json_fence(raw)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse Claude JSON for %s: %s", item.title[:40], e)
            return AnalysisResult(
                summary=BilingualText(ja=raw[:500]),
                error=f"JSON パース失敗: {e}",
            )

        sources: list[RelatedSource] = []
        for src in data.get("sources", []):
            try:
                sources.append(RelatedSource(title=src["title"], url=src["url"]))
            except (KeyError, ValueError):
                continue

        return AnalysisResult(
            summary=_as_bilingual(data.get("summary")),
            evidence=_as_bilingual(data.get("evidence")),
            background=_as_bilingual(data.get("background")),
            caveats=_as_bilingual(data.get("caveats")),
            outlook=_as_bilingual(data.get("outlook")),
            sources=sources,
        )


def _as_bilingual(value) -> BilingualText:
    if isinstance(value, dict):
        return BilingualText(
            ja=str(value.get("ja", "")).strip(),
            en=str(value.get("en", "")).strip(),
        )
    if isinstance(value, str):
        return BilingualText(ja=value.strip())
    return BilingualText()


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text
