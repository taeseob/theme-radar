#!/usr/bin/env python3
"""섹터 추세가 다음 기간에도 이어졌는지 DB의 기간 수익률로 확인하는 스크립트.

화면이 보여 주는 값으로 투자 판단을 할 수 있는지 사후에 따져 보려고 둔다.
계산은 확정 기간(`is_provisional = 0`)의 그룹 시총가중 수익률(group_period_stat.ret)만 쓴다.
그룹을 동일가중으로 담고, 거래비용·세금은 넣지 않는다.

두 가지를 본다.

momentum  형성 L기간 누적수익 상위 20% / 하위 20% 그룹을 H기간 들고 있었을 때 차이,
          형성 수익 순위와 다음 수익 순위의 스피어만 상관(IC). IC t값은 H기간 간격으로 뽑은
          겹치지 않는 표본으로 낸다.
pullback  형성 13기간 상위 20% 그룹을 단기 눌림 여부로 나눠 다음 4·13기간 수익을 비교한다.
          수익은 같은 날 모든 그룹 평균(동일가중) 대비다.

사용법::

    python scripts/check_momentum.py                     # 두 보고서, 네 스킴, 주 단위
    python scripts/check_momentum.py --report pullback --scheme WI26 --scheme GICS

의존성: 표준 라이브러리와 theme_radar.config(DB 경로)만 쓴다.
"""

from __future__ import annotations

import argparse
import sqlite3
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from theme_radar.config import load_config, resolve_path  # noqa: E402

# THEME_KR은 오늘 구성을 과거에 적용한 소급 계산이라 결과가 실제보다 좋게 나오기 쉽다 (docs/02 §9 S-20)
SCHEMES = {"WI26": "KR_COMMON", "WI26_SUB": "KR_COMMON", "THEME_KR": "KR_COMMON",
           "GICS": "US_SP500", "GICS_IND": "US_SP500"}
QUANTILE = 0.2
TOP_LOOKBACK = 13


def load(conn: sqlite3.Connection, universe: str, scheme: str, period_type: str):
    """확정 기간 순서와 그룹별 {period_seq: ret}를 읽는다."""
    seqs = [s for (s,) in conn.execute(
        "SELECT period_seq FROM universe_period_stat WHERE universe_code = ? AND period_type = ? "
        "AND is_provisional = 0 ORDER BY period_seq", (universe, period_type))]
    groups: dict[str, dict[int, float]] = defaultdict(dict)
    for code, seq, ret in conn.execute(
            "SELECT group_code, period_seq, ret FROM group_period_stat WHERE universe_code = ? AND scheme_code = ? "
            "AND period_type = ? AND is_provisional = 0 AND group_code <> 'UNMAPPED'",
            (universe, scheme, period_type)):
        groups[code][seq] = ret
    return seqs, groups


def compound(rets) -> float:
    value = 1.0
    for r in rets:
        value *= 1 + r
    return value - 1


def ranks(xs: list[float]) -> list[int]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0] * len(xs)
    for k, i in enumerate(order):
        out[i] = k
    return out


def spearman(a: list[float], b: list[float]) -> float:
    ra, rb = ranks(a), ranks(b)
    ma, mb = st.mean(ra), st.mean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den if den else 0.0


def tstat(xs: list[float]) -> float:
    if len(xs) < 3 or st.stdev(xs) == 0:
        return float("nan")
    return st.mean(xs) / (st.stdev(xs) / len(xs) ** 0.5)


def pct(x: float) -> str:
    return f"{x * 100:+.2f}%"


def momentum_report(seqs, groups) -> None:
    print(f"{'형성':>4} {'보유':>4} {'표본':>4} {'상위20%':>9} {'하위20%':>9} {'상-하':>9} {'IC':>7} {'IC t':>6}")
    for lookback in (1, 4, 13, 26):
        for hold in (1, 4, 13):
            tops, bots, ic = [], [], {}
            for i in range(lookback - 1, len(seqs) - hold):
                past, fut = seqs[i - lookback + 1:i + 1], seqs[i + 1:i + 1 + hold]
                names = [g for g in groups if all(s in groups[g] for s in past + fut)]
                if len(names) < 5:
                    continue
                formed = [compound(groups[g][s] for s in past) for g in names]
                fwd = [compound(groups[g][s] for s in fut) for g in names]
                avg = st.mean(fwd)
                q = max(1, round(len(names) * QUANTILE))
                order = sorted(range(len(names)), key=lambda j: formed[j], reverse=True)
                tops.append(st.mean(fwd[j] for j in order[:q]) - avg)
                bots.append(st.mean(fwd[j] for j in order[-q:]) - avg)
                ic[i] = spearman(formed, fwd)
            sample = [ic[k] for k in sorted(ic)[::hold]]
            print(f"{lookback:>4} {hold:>4} {len(tops):>4} {pct(st.mean(tops)):>9} {pct(st.mean(bots)):>9} "
                  f"{pct(st.mean(tops) - st.mean(bots)):>9} {st.mean(ic.values()):>7.3f} {tstat(sample):>6.2f}")


def pullback_flags(index: list[float]) -> dict[str, bool]:
    """형성 구간 끝의 수익률 지수(마지막 값이 현재)로 단기 눌림 조건을 판정한다."""
    now = index[-1]
    ma5 = st.mean(index[-5:])
    return {
        "지난 1주 하락": now < index[-2],
        "5주 이평 아래": now < ma5,
        "4주 수익 음수": now < index[-5],
        "4주 고점 대비 -5%": now <= max(index[-5:]) * 0.95,
    }


def pullback_report(seqs, groups) -> None:
    holds = (4, 13)
    buckets: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    weeks: dict[str, set[int]] = defaultdict(set)
    for i in range(TOP_LOOKBACK, len(seqs) - 1):
        past = seqs[i - TOP_LOOKBACK:i + 1]            # 지수 시작점 하나를 더 둔다
        names = [g for g in groups if all(s in groups[g] for s in past)]
        if len(names) < 5:
            continue
        formed = {g: compound(groups[g][s] for s in past[1:]) for g in names}
        q = max(1, round(len(names) * QUANTILE))
        top = sorted(names, key=lambda g: formed[g], reverse=True)[:q]
        for hold in holds:
            fut = seqs[i + 1:i + 1 + hold]
            if len(fut) < hold:
                continue
            alive = [g for g in names if all(s in groups[g] for s in fut)]
            avg = st.mean(compound(groups[g][s] for s in fut) for g in alive)
            for g in top:
                if g not in alive:
                    continue
                index, value = [1.0], 1.0
                for s in past[1:]:
                    value *= 1 + groups[g][s]
                    index.append(value)
                excess = compound(groups[g][s] for s in fut) - avg
                buckets["상위 전체"][hold].append(excess)
                for name, hit in pullback_flags(index).items():
                    key = f"{name}" if hit else f"{name} 아님"
                    buckets[key][hold].append(excess)
                    if hit and hold == holds[0]:
                        weeks[name].add(i)
    print(f"{'조건':<20} {'표본':>5} {'주 수':>5} {'4주 후':>9} {'13주 후':>9} {'13주 승률':>9}")
    order = ["상위 전체"]
    for name in ("지난 1주 하락", "5주 이평 아래", "4주 수익 음수", "4주 고점 대비 -5%"):
        order += [name, f"{name} 아님"]
    for key in order:
        b = buckets[key]
        if not b[holds[0]]:
            print(f"{key:<20} {0:>5}")
            continue
        win = sum(x > 0 for x in b[13]) / len(b[13]) if b[13] else float("nan")
        n_weeks = len(weeks[key]) if key in weeks else ""
        print(f"{key:<20} {len(b[holds[0]]):>5} {n_weeks:>5} {pct(st.mean(b[4])):>9} "
              f"{pct(st.mean(b[13])) if b[13] else '':>9} {win * 100:>8.0f}%")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scheme", action="append", choices=sorted(SCHEMES),
                        help="확인할 스킴. 여러 번 줄 수 있다 (기본: 섹터 스킴 네 개. THEME_KR은 소급이라 따로 준다)")
    parser.add_argument("--report", choices=("momentum", "pullback", "all"), default="all")
    parser.add_argument("--period", choices=("W", "M"), default="W")
    args = parser.parse_args()

    conn = sqlite3.connect(resolve_path(load_config()["db"]["path"]))
    for scheme in args.scheme or [s for s in SCHEMES if s != "THEME_KR"]:
        seqs, groups = load(conn, SCHEMES[scheme], scheme, args.period)
        print(f"\n=== {SCHEMES[scheme]} {scheme} {args.period}  확정 {len(seqs)}기간, 그룹 {len(groups)}개 ===")
        print("수익은 같은 기간 전체 그룹 평균(동일가중) 대비")
        if args.report in ("momentum", "all"):
            momentum_report(seqs, groups)
        if args.report in ("pullback", "all"):
            print(f"\n[{TOP_LOOKBACK}기간 상위 {QUANTILE:.0%} + 단기 눌림]")
            pullback_report(seqs, groups)
    return 0


if __name__ == "__main__":
    sys.exit(main())
