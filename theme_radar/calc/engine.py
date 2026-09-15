"""기간 하나의 지표 계산 (docs/03). 입출력이 없는 순수 함수만 둔다.

합계는 math.fsum으로 구한다. 검증 규칙(V-1 ~ V-4)의 허용오차가 1e-9라 단순 누적으로는 오차가 남는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import fsum
from statistics import median

UNMAPPED = "UNMAPPED"
CONTRIB_SHARE_GUARD = 0.0005      # |유니버스 수익률| 5bp 미만이면 기여 비중을 두지 않는다 (docs/03 §7.3)
INCLUDED_STATUSES = ("INCLUDED", "SUSPENDED", "DELISTED")   # 계산에 넣는 상태 (docs/03 §10)
TOP_K = (1, 3, 5)


@dataclass
class Member:
    security_id: int
    group_code: str
    base_close_adj: float | None
    end_close_adj: float | None
    base_market_cap: float | None
    incl_status: str
    ret: float | None = None
    weight_universe: float | None = None
    weight_in_group: float | None = None
    contribution: float | None = None


@dataclass
class GroupStat:
    group_code: str
    ret: float
    ret_equal: float
    ret_median: float
    base_weight: float
    contribution: float
    contrib_share: float | None
    member_cnt: int
    up_cnt: int
    hhi: float
    effective_n: float
    top_shares: dict[int, float | None]
    top_neg_shares: dict[int, float | None]
    cap_weight_spread: float
    rank_ret: int | None = None
    rank_contrib: int | None = None
    members: list[Member] = field(default_factory=list)


@dataclass
class UniverseStat:
    ret: float
    ret_equal: float
    ret_median: float
    base_market_cap: float
    member_cnt: int
    up_cnt: int
    unmapped_cap_ratio: float


@dataclass
class PeriodResult:
    universe: UniverseStat
    groups: list[GroupStat]
    members: list[Member]          # 제외 종목 포함 (사유는 incl_status)


def competition_rank(values: list[float], reverse: bool = True) -> list[int]:
    """경쟁 순위. 동점이면 같은 순위를 주고 다음 순위를 건너뛴다 (1, 2, 2, 4) (docs/03 §6)."""
    order = sorted(range(len(values)), key=lambda i: values[i], reverse=reverse)
    ranks = [0] * len(values)
    previous, previous_rank = None, 0
    for position, i in enumerate(order, 1):
        if previous is None or values[i] != previous:
            previous, previous_rank = values[i], position
        ranks[i] = previous_rank
    return ranks


def _top_share(contributions: list[float], positive: bool) -> dict[int, float | None]:
    """상위(하위) K개 기여의 합을 양(음)의 기여 총합으로 나눈 값 (docs/03 §9.1)."""
    side = [c for c in contributions if (c > 0 if positive else c < 0)]
    total = fsum(side)
    if not side or total == 0:
        return {k: None for k in TOP_K}
    ordered = sorted(side, reverse=positive)
    return {k: fsum(ordered[:k]) / total for k in TOP_K}


def compute(members: list[Member]) -> PeriodResult:
    """유니버스 구성원으로 종목 수익률, 그룹 통계, 순위를 만든다.

    가중치는 기준일 시가총액이고, 제외된 종목은 분모에서도 빠진다 (docs/03 §3, §10).
    """
    included = [m for m in members if m.incl_status in INCLUDED_STATUSES]
    for m in included:
        m.ret = m.end_close_adj / m.base_close_adj - 1
    total_cap = fsum(m.base_market_cap for m in included)
    for m in included:
        m.weight_universe = m.base_market_cap / total_cap

    universe = UniverseStat(
        ret=fsum(m.weight_universe * m.ret for m in included),
        ret_equal=fsum(m.ret for m in included) / len(included),
        ret_median=median(m.ret for m in included),
        base_market_cap=total_cap,
        member_cnt=len(included),
        up_cnt=sum(1 for m in included if m.ret > 0),
        unmapped_cap_ratio=fsum(m.base_market_cap for m in included if m.group_code == UNMAPPED) / total_cap,
    )

    by_group: dict[str, list[Member]] = {}
    for m in included:
        by_group.setdefault(m.group_code, []).append(m)

    groups = []
    for group_code, group_members in sorted(by_group.items()):
        group_cap = fsum(m.base_market_cap for m in group_members)
        for m in group_members:
            m.weight_in_group = m.base_market_cap / group_cap
            m.contribution = m.weight_in_group * m.ret
        contributions = [m.contribution for m in group_members]
        rets = [m.ret for m in group_members]
        group_ret = fsum(contributions)
        ret_equal = fsum(rets) / len(rets)
        base_weight = group_cap / total_cap
        groups.append(GroupStat(
            group_code=group_code,
            ret=group_ret,
            ret_equal=ret_equal,
            ret_median=median(rets),
            base_weight=base_weight,
            contribution=base_weight * group_ret,
            contrib_share=None,
            member_cnt=len(group_members),
            up_cnt=sum(1 for r in rets if r > 0),
            hhi=fsum(m.weight_in_group ** 2 for m in group_members),
            effective_n=1 / fsum(m.weight_in_group ** 2 for m in group_members),
            top_shares=_top_share(contributions, positive=True),
            top_neg_shares=_top_share(contributions, positive=False),
            cap_weight_spread=group_ret - ret_equal,
            members=sorted(group_members, key=lambda m: -m.contribution),
        ))

    # 순위는 분류 체계의 섹터에만 준다. 미매핑 의사 그룹은 순위를 갖지 않는다
    ranked = [g for g in groups if g.group_code != UNMAPPED]
    if ranked:
        # 동점 tie-break: 수익률 desc → 기준 비중 desc → 그룹 코드 asc (docs/03 §6)
        ordered = sorted(ranked, key=lambda g: (-g.ret, -g.base_weight, g.group_code))
        for g, rank in zip(ordered, competition_rank([g.ret for g in ordered])):
            g.rank_ret = rank
        contrib_ordered = sorted(ranked, key=lambda g: (-abs(g.contribution), g.group_code))
        for g, rank in zip(contrib_ordered, competition_rank([abs(g.contribution) for g in contrib_ordered])):
            g.rank_contrib = rank
    if abs(universe.ret) >= CONTRIB_SHARE_GUARD:
        for g in groups:
            g.contrib_share = g.contribution / universe.ret

    return PeriodResult(universe=universe, groups=groups, members=members)
