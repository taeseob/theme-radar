"""계산 검증 V-1 ~ V-9 (docs/03 §12). 입출력 없는 순수 함수."""
from __future__ import annotations

from dataclasses import dataclass
from math import fsum

from theme_radar.calc.engine import PeriodResult

TOLERANCE = 1e-9
UNMAPPED_CAP_LIMIT = 0.005
RETURN_LIMIT = {"W": 1.0, "M": 2.0}


@dataclass(frozen=True)
class Violation:
    rule_code: str
    severity: str          # BLOCK | WARN
    observed: float | None
    tolerance: float | None
    detail: str


def check(result: PeriodResult, period_type: str, exclusive_scheme: bool = True) -> list[Violation]:
    """위반 목록. BLOCK이 하나라도 있으면 그 기간은 커밋하지 않는다."""
    out: list[Violation] = []
    included = [m for m in result.members if m.weight_universe is not None]

    def block(rule, observed, detail, tolerance=TOLERANCE):
        out.append(Violation(rule, "BLOCK", observed, tolerance, detail))

    weight_sum = fsum(m.weight_universe for m in included)
    if abs(weight_sum - 1) > TOLERANCE:
        block("V-1", weight_sum, f"유니버스 비중 합이 1이 아니다: {weight_sum!r}")

    for g in result.groups:
        group_weight = fsum(m.weight_in_group for m in g.members)
        if abs(group_weight - 1) > TOLERANCE:
            block("V-2", group_weight, f"{g.group_code} 그룹 내 비중 합이 1이 아니다: {group_weight!r}")

    if exclusive_scheme:
        contribution_sum = fsum(g.contribution for g in result.groups)
        if abs(contribution_sum - result.universe.ret) > TOLERANCE:
            block("V-3", contribution_sum - result.universe.ret,
                  f"기여도 합 {contribution_sum!r} ≠ 유니버스 수익률 {result.universe.ret!r}")

    for g in result.groups:
        member_sum = fsum(m.contribution for m in g.members)
        if abs(member_sum - g.ret) > TOLERANCE:
            block("V-4", member_sum - g.ret, f"{g.group_code} 종목 기여도 합 {member_sum!r} ≠ 그룹 수익률 {g.ret!r}")

    if exclusive_scheme:
        group_members = sum(g.member_cnt for g in result.groups)
        if group_members != result.universe.member_cnt:
            block("V-5", group_members, f"그룹 종목 수 합 {group_members} ≠ 유니버스 종목 수 {result.universe.member_cnt}", None)

    # 경쟁 순위(1, 2, 2, 4)는 정렬했을 때 새 순위 값이 처음 나오는 자리가 곧 그 순위다
    ranks = sorted(g.rank_ret for g in result.groups if g.rank_ret is not None)
    for i, rank in enumerate(ranks):
        if (i == 0 or ranks[i - 1] != rank) and rank != i + 1:
            block("V-6", rank, f"경쟁 순위가 1부터 시작하는 연속 순위가 아니다: {ranks}", None)
            break

    for g in result.groups:
        if not g.hhi > 0 or not 0 < g.effective_n <= g.member_cnt + TOLERANCE:
            block("V-7", g.effective_n, f"{g.group_code} 집중도 값이 범위를 벗어난다 (hhi {g.hhi}, 유효종목수 {g.effective_n}, 종목 {g.member_cnt})", None)

    if result.universe.unmapped_cap_ratio > UNMAPPED_CAP_LIMIT:
        out.append(Violation("V-8", "WARN", result.universe.unmapped_cap_ratio, UNMAPPED_CAP_LIMIT,
                             f"분류 미매핑 시총 비중 {result.universe.unmapped_cap_ratio:.2%}"))

    limit = RETURN_LIMIT[period_type]
    outliers = [(m.security_id, round(m.ret, 4)) for m in included if abs(m.ret) > limit]
    if outliers:
        out.append(Violation("V-9", "WARN", len(outliers), limit,
                             f"수익률 이상치 {len(outliers)}종목 (security_id, 수익률): {sorted(outliers, key=lambda x: -abs(x[1]))[:20]}"))
    return out
