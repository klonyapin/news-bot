from __future__ import annotations

import json
import logging
import os

from anthropic import AsyncAnthropic

from src.models import AnalysisResult, BilingualText, NewsItem, RelatedSource


logger = logging.getLogger(__name__)


DEFAULT_MODEL = "claude-sonnet-5"
MAX_TOKENS = 16000
MAX_PAUSE_CONTINUATIONS = 3


SYSTEM_PROMPT = """あなたは日本の政治・国際・経済ニュースを鋭く読み解く報道アナリストです。

与えられた記事について、Web 検索で一次情報を確認したうえで、以下の 5 軸を日英併記で解説してください。

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

**出力フォーマット (厳守):**
必ず単一の JSON オブジェクトのみを返す。JSON の前後に文章・見出し・コードフェンスを一切付けない。

{
  "summary":    {"ja": "...", "en": "..."},
  "evidence":   {"ja": "...", "en": "..."},
  "background": {"ja": "...", "en": "..."},
  "caveats":    {"ja": "...", "en": "..."},
  "outlook":    {"ja": "...", "en": "..."},
  "sources": [
    {"title": "...", "url": "https://..."}
  ]
}

sources は Web 検索で参照した情報源を最大 3 件。憶測になる部分は "〜とみられる" と明示。
各テキストは日英とも 300 文字前後に収める (合計出力トークンを 8000 以下に)。"""


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

        messages = [{"role": "user", "content": user_message}]
        tools = [
            {
                "type": "web_search_20260209",
                "name": "web_search",
                "max_uses": 3,
            }
        ]

        try:
            response = await self._call_with_pause_handling(messages, tools)
        except Exception as e:
            logger.error("Claude API error for %s: %s", item.title[:40], e)
            return AnalysisResult(
                summary=BilingualText(ja=item.summary or item.title),
                error=f"分析エラー: {type(e).__name__}: {e}",
            )

        return self._parse_response(response, item)

    async def _call_with_pause_handling(self, messages, tools):
        """pause_turn / refusal を含む完了パターンを扱いつつ streaming で呼ぶ。"""
        for attempt in range(MAX_PAUSE_CONTINUATIONS + 1):
            async with self.client.messages.stream(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                tools=tools,
                messages=messages,
            ) as stream:
                response = await stream.get_final_message()

            if response.stop_reason == "pause_turn":
                if attempt >= MAX_PAUSE_CONTINUATIONS:
                    logger.warning("pause_turn but max continuations reached")
                    return response
                logger.info("pause_turn received, continuing (attempt %d)", attempt + 1)
                messages = messages + [{"role": "assistant", "content": response.content}]
                continue
            return response
        return response

    def _parse_response(self, response, item: NewsItem) -> AnalysisResult:
        stop_reason = getattr(response, "stop_reason", "unknown")

        if stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            reason = getattr(details, "explanation", "") if details else ""
            logger.warning("Refused: %s (%s)", item.title[:40], reason)
            return AnalysisResult(
                summary=BilingualText(ja=item.summary or item.title),
                error=f"Claude が分析を拒否 (safety filter): {reason or 'no explanation'}",
            )

        text_blocks = [block.text for block in response.content if block.type == "text"]
        block_types = [b.type for b in response.content]

        if not text_blocks:
            logger.warning(
                "No text block. stop_reason=%s, blocks=%s, title=%s",
                stop_reason, block_types, item.title[:40],
            )
            return AnalysisResult(
                summary=BilingualText(ja=item.summary or item.title),
                error=f"Claude 応答に text ブロックなし (stop_reason={stop_reason}, blocks={block_types})",
            )

        raw = "".join(text_blocks).strip()
        raw = _strip_json_fence(raw)

        if not raw:
            logger.warning(
                "Empty text after strip. stop_reason=%s, title=%s",
                stop_reason, item.title[:40],
            )
            return AnalysisResult(
                summary=BilingualText(ja=item.summary or item.title),
                error=f"Claude 応答が空 (stop_reason={stop_reason})",
            )

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning(
                "JSON parse failed for %s: %s | stop_reason=%s | raw[:200]=%r",
                item.title[:40], e, stop_reason, raw[:200],
            )
            # Try lenient parse: extract first {...} block
            data = _try_lenient_parse(raw)
            if data is None:
                return AnalysisResult(
                    summary=BilingualText(ja=raw[:500]),
                    error=f"JSON パース失敗: {e} (stop_reason={stop_reason})",
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


def _try_lenient_parse(text: str):
    """先頭 { から始まる部分を切り出して parse。truncation 対応。"""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == "\\" and in_str:
            escape = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None
