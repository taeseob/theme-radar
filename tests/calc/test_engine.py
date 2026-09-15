"""docs/03의 계산을 손계산과 대조한다."""
import pytest

from theme_radar.calc import engine, validate


def member(sid, group, base, end, cap, status="INCLUDED"):
    return engine.Member(sid, group, base, end, cap, status)


def sample():
    """A +10% (시총 1000), B -10% (3000) → G1 / C +10% (2000) → G2. 유니버스 수익률은 정확히 0이다."""
    return [member(1, "G1", 100, 110, 1000), member(2, "G1", 200, 180, 3000), member(3, "G2", 50, 55, 2000)]


def test_competition_rank_skips_after_ties():
    assert engine.competition_rank([0.3, 0.1, 0.1, -0.2]) == [1, 2, 2, 4]


def test_universe_and_group_statistics():
    result = engine.compute(sample())
    u = result.universe
    assert u.base_market_cap == 6000 and u.member_cnt == 3 and u.up_cnt == 2
    assert u.ret == pytest.approx(0.0, abs=1e-15)
    assert u.ret_equal == pytest.approx(0.1 / 3)
    assert u.ret_median == pytest.approx(0.1)
    assert u.unmapped_cap_ratio == 0

    g1, g2 = result.groups
    assert (g1.group_code, g2.group_code) == ("G1", "G2")
    assert g1.ret == pytest.approx(-0.05)             # 0.25 × +10% + 0.75 × −10%
    assert g1.ret_equal == pytest.approx(0.0)
    assert g1.base_weight == pytest.approx(2 / 3)
    assert g1.contribution == pytest.approx(-1 / 30)
    assert g1.hhi == pytest.approx(0.625) and g1.effective_n == pytest.approx(1.6)
    assert g1.top_shares[1] == pytest.approx(1.0)     # 양의 기여는 A 하나뿐
    assert g1.top_neg_shares[1] == pytest.approx(1.0)
    assert g1.cap_weight_spread == pytest.approx(-0.05)
    assert g2.ret == pytest.approx(0.1) and g2.effective_n == pytest.approx(1.0)
    assert g2.top_neg_shares[1] is None               # 음의 기여가 없다


def test_contribution_sums_to_universe_return():
    result = engine.compute(sample())
    assert sum(g.contribution for g in result.groups) == pytest.approx(result.universe.ret, abs=1e-15)


def test_contrib_share_is_none_near_zero_universe_return():
    result = engine.compute(sample())
    assert all(g.contrib_share is None for g in result.groups)

    moved = engine.compute([member(1, "G1", 100, 120, 1000), member(2, "G2", 100, 105, 1000)])
    assert moved.universe.ret == pytest.approx(0.125)
    assert sum(g.contrib_share for g in moved.groups) == pytest.approx(1.0)


def test_ranks_use_tie_break_and_skip_unmapped():
    members = [member(1, "G1", 100, 110, 1000), member(2, "G2", 100, 110, 3000),
               member(3, "G3", 100, 90, 500), member(4, engine.UNMAPPED, 100, 200, 100)]
    result = {g.group_code: g for g in engine.compute(members).groups}
    # 동점은 같은 순위를 받고 다음 순위를 건너뛴다. tie-break(기준 비중)는 순위 값이 아니라 정렬 순서를 정한다
    assert (result["G1"].rank_ret, result["G2"].rank_ret, result["G3"].rank_ret) == (1, 1, 3)
    assert result[engine.UNMAPPED].rank_ret is None
    assert result["G2"].rank_contrib == 1            # 기여도는 시총이 큰 G2가 크다


def test_excluded_members_do_not_affect_weights():
    members = sample() + [member(9, "G1", None, None, None, "NEW_LISTING"), member(8, "G2", 10, 11, 0, "NO_MCAP")]
    result = engine.compute(members)
    assert result.universe.member_cnt == 3 and result.universe.base_market_cap == 6000
    assert [m.weight_universe for m in result.members if m.security_id in (8, 9)] == [None, None]


def test_suspended_and_delisted_members_are_included():
    members = [member(1, "G1", 100, 100, 1000, "SUSPENDED"), member(2, "G1", 100, 90, 1000, "DELISTED")]
    result = engine.compute(members)
    assert result.universe.member_cnt == 2
    assert result.groups[0].ret == pytest.approx(-0.05)


def test_validation_passes_on_valid_result():
    assert validate.check(engine.compute(sample()), "W") == []


def test_validation_warns_on_unmapped_share_and_outliers():
    members = [member(1, "G1", 100, 110, 1000), member(2, engine.UNMAPPED, 100, 400, 1000)]
    violations = {v.rule_code: v for v in validate.check(engine.compute(members), "W")}
    assert violations["V-8"].severity == "WARN" and violations["V-8"].observed == pytest.approx(0.5)
    assert violations["V-9"].severity == "WARN" and violations["V-9"].observed == 1


def test_validation_blocks_when_contributions_do_not_add_up():
    result = engine.compute(sample())
    result.groups[0].contribution += 1e-6           # 계산을 깨뜨린다
    violations = [v for v in validate.check(result, "W") if v.severity == "BLOCK"]
    assert [v.rule_code for v in violations] == ["V-3"]
