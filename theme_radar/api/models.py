"""응답 모델 (docs/05). 수치 반올림은 §1.4를 따른다: 수익률·기여도 8자리, 비중 10자리."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

RETURN_DIGITS = 8
WEIGHT_DIGITS = 10


def r8(value: float | None) -> float | None:
    return None if value is None else round(value, RETURN_DIGITS)


def r10(value: float | None) -> float | None:
    return None if value is None else round(value, WEIGHT_DIGITS)


class Model(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class Meta(Model):
    """엔드포인트마다 필요한 값을 더 넣는다 (from, to, rank_by, period_id 등)."""
    model_config = ConfigDict(extra="allow")

    universe: str
    period: str
    currency: str
    scheme: str | None = None
    calc_version: str | None = None
    calculated_at: str | None = None
    stale: bool = False


class RankPoint(Model):
    period_id: str
    rank: int
    rank_delta: int | None = None
    ret: float | None = Field(default=None, alias="return")
    base_weight: float | None = None
    contribution: float | None = None
    top1_contrib_share: float | None = None
    member_cnt: int


class RankSeries(Model):
    group_code: str
    name: str
    color: str | None = None
    points: list[RankPoint]


class RanksPeriod(Model):
    period_id: str
    end_date: str
    is_provisional: bool
    universe_return: float | None = None
    group_count: int


class RanksData(Model):
    periods: list[RanksPeriod]
    series: list[RankSeries]
    others_count: int = 0


class RanksResponse(Model):
    meta: Meta
    data: RanksData


class SectorRow(Model):
    group_code: str
    name: str
    color: str | None = None
    ret: float | None = Field(default=None, alias="return")
    return_equal: float | None = None
    return_median: float | None = None
    base_weight: float | None = None
    contribution: float | None = None
    contribution_share: float | None = None
    rank: int | None = None
    rank_prev: int | None = None
    rank_delta: int | None = None
    member_cnt: int
    up_cnt: int
    up_ratio: float | None = None
    hhi: float | None = None
    effective_n: float | None = None
    top1_contrib_share: float | None = None
    top3_contrib_share: float | None = None
    top5_contrib_share: float | None = None
    cap_weight_spread: float | None = None
    badges: list[str] = []


class ReturnsResponse(Model):
    meta: Meta
    data: list[SectorRow]


class Contributor(Model):
    group_code: str
    name: str
    contribution: float | None = None


class MarketSummary(Model):
    period_id: str
    base_date: str | None = None
    end_date: str
    is_provisional: bool
    universe_return: float | None = None
    universe_return_equal: float | None = None
    universe_return_median: float | None = None
    member_cnt: int
    up_cnt: int
    up_ratio: float | None = None
    unmapped_cap_ratio: float | None = None
    top_contributors: list[Contributor] = []
    bottom_contributors: list[Contributor] = []
    disclaimer: str


class SummaryResponse(Model):
    meta: Meta
    data: MarketSummary


class BreakdownSummary(Model):
    ret: float | None = Field(default=None, alias="return")
    return_equal: float | None = None
    cap_weight_spread: float | None = None
    base_weight: float | None = None
    contribution: float | None = None
    member_cnt: int
    up_cnt: int
    up_ratio: float | None = None
    hhi: float | None = None
    effective_n: float | None = None
    top1_contrib_share: float | None = None
    top3_contrib_share: float | None = None
    top5_contrib_share: float | None = None
    badges: list[str] = []


class BreakdownMember(Model):
    security_id: int
    ticker: str
    name: str
    weight_in_group: float | None = None
    weight_in_universe: float | None = None
    ret: float | None = Field(default=None, alias="return")
    contribution_in_group: float | None = None
    contribution_in_universe: float | None = None
    contrib_rank: int | None = None


class BreakdownOthers(Model):
    member_cnt: int
    weight_in_group: float | None = None
    contribution_in_group: float | None = None


class BreakdownData(Model):
    summary: BreakdownSummary
    members: list[BreakdownMember]
    others: BreakdownOthers


class BreakdownResponse(Model):
    meta: Meta
    data: BreakdownData


class HistoryPoint(Model):
    period_id: str
    ret: float | None = Field(default=None, alias="return")
    rank: int | None = None
    contribution: float | None = None
    base_weight: float | None = None
    top1_contrib_share: float | None = None
    hhi: float | None = None
    up_ratio: float | None = None
    cap_weight_spread: float | None = None
    is_provisional: bool = False


class HistoryResponse(Model):
    meta: Meta
    data: list[HistoryPoint]


class SecurityHit(Model):
    security_id: int
    ticker: str
    name: str
    board: str
    group_code: str | None = None
    group_name: str | None = None


class SpecialEvent(Model):
    event_id: int
    market: str
    event_date: str
    end_date: str | None = None
    event_type: str
    ticker: str | None = None
    name: str | None = None
    group_code: str | None = None
    group_name: str | None = None
    market_cap: float | None = None
    sector_share: float | None = None
    detail: str


class ListResponse(Model):
    data: list[Any]
