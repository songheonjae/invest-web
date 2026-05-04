from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


# ──────────────────────────────────────────────
# Request
# ──────────────────────────────────────────────
class RecommendRequest(BaseModel):
    amount: int = Field(..., gt=0, description="투자금액 (원)")
    risk: Literal["conservative", "neutral", "aggressive"]
    period: Literal["short", "medium", "long"]
    market: Literal["KR", "US", "ALL"]
    recommend_count: int | None = Field(default=None, ge=1, le=5, description="추천 종목 개수 (None=자동)")
    prefer_sectors: list[str] = Field(default_factory=list, description="관심 업종/사업")
    prefer_styles: list[str] = Field(default_factory=list, description="선호 투자 스타일")
    exclude_sectors: list[str] = Field(default_factory=list, description="제외 업종")
    exclude_tags: list[str] = Field(default_factory=list, description="제외 조건 (리스크/성격)")
    holdings: list[str] = Field(default_factory=list, description="보유 중인 종목 ticker 목록")


# ──────────────────────────────────────────────
# Response — 중첩 모델
# ──────────────────────────────────────────────
class SplitEntry(BaseModel):
    차수: str
    금액: int
    조건: str


class SplitPlanItem(BaseModel):
    ticker: str
    name: str
    sector: str
    tier: int
    weight_pct: float
    amount_krw: int
    split: list[SplitEntry]


class RecommendationItem(BaseModel):
    ticker: str
    name: str
    sector: str
    tier: int
    score: float
    reasons: list[str]
    risks: list[str]
    buy_type: Literal["즉시 검토형", "확인 후 매수형", "관찰 우선형"]
    weight_pct: float | None = None
    amount_krw: int | None = None
    split: list[SplitEntry] = Field(default_factory=list)


class ExcludedItem(BaseModel):
    ticker: str
    name: str
    sector: str
    score: float
    exclusion_reason: str


class RecommendResponse(BaseModel):
    summary: str
    recommendations: list[RecommendationItem]
    excluded: list[ExcludedItem]
    split_buy_plan: list[SplitPlanItem]
    notes: str
    report: str
