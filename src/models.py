from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


Category = Literal["domestic", "world", "business"]


class NewsItem(BaseModel):
    """Yahoo! ニュースの 1 記事。ソースからの生データ。"""

    title: str
    url: HttpUrl
    summary: str = ""
    category: Category
    published_at: datetime | None = None
    ranking_position: int | None = None
    trending_keywords: list[str] = Field(default_factory=list)
    importance: int = 0

    @property
    def heat_score(self) -> float:
        """記事のヒート度。並び替えに使う。"""
        score = 0.0
        if self.ranking_position is not None:
            score += max(0.0, 30.0 - self.ranking_position)
        score += len(self.trending_keywords) * 5.0
        if self.published_at is not None:
            age_hours = (datetime.now(self.published_at.tzinfo) - self.published_at).total_seconds() / 3600
            score += max(0.0, 12.0 - age_hours) * 0.5
        return score


class RelatedSource(BaseModel):
    """Claude が Web 検索で見つけた関連情報源。"""

    title: str
    url: HttpUrl


class BilingualText(BaseModel):
    """日英併記のテキスト。"""

    ja: str = ""
    en: str = ""


class AnalysisResult(BaseModel):
    """Claude による解説結果 (4 軸 × 日英)。"""

    summary: BilingualText = Field(default_factory=BilingualText)
    evidence: BilingualText = Field(default_factory=BilingualText)
    background: BilingualText = Field(default_factory=BilingualText)
    caveats: BilingualText = Field(default_factory=BilingualText)
    outlook: BilingualText = Field(default_factory=BilingualText)
    sources: list[RelatedSource] = Field(default_factory=list)
    error: str | None = None
