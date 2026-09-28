// @ts-check
/**
 * N기간 누적수익 (docs/03 §16). 저장하지 않고 화면이 섹터의 기간 수익률을 복리로 잇는다.
 * 기간 수익률은 시총가중 가격 수익률이라, 이동평균 상승률(ma.js)과 달리 상장·폐지 같은 가격 외 변화가 들어가지 않는다.
 */
import { assignRanks } from "./ma.js";

/**
 * 섹터별 N기간 누적수익을 구하고, 기간마다 그 순위를 매긴다.
 * 창 안에 수익률이 빈 기간이 하나라도 있으면 그 자리는 비운다 (보간하지 않는다).
 *
 * @param {any} payload 앞 기간까지 받은 /sectors/ranks 응답 (top_n 없이)
 * @param {any[]} periods 앞 기간을 포함한 기간 행 (period_seq 오름차순). 값은 이 배열에 맞춰 늘어선다
 * @param {number} n 누적 기간 수
 * @returns {Map<string, {name: string, color: string|null, points: (any|null)[]}>}
 */
export function build(payload, periods, n) {
  const index = new Map(periods.map((p, i) => [p.period_id, i]));
  const groups = new Map();
  for (const line of payload.data.series) {
    const returns = /** @type {(number|null)[]} */ (new Array(periods.length).fill(null));
    const members = new Array(periods.length).fill(null);
    for (const point of line.points) {
      const i = index.get(point.period_id);
      if (i === undefined || point["return"] === null || point["return"] === undefined) continue;
      returns[i] = point["return"];
      members[i] = point.member_cnt;
    }
    const points = periods.map((p, i) => {
      if (i < n - 1) return null;
      const window = returns.slice(i - n + 1, i + 1);
      if (window.some((r) => r === null)) return null;
      const value = window.reduce((acc, r) => acc * (1 + /** @type {number} */ (r)), 1) - 1;
      return { period_id: p.period_id, ret: value, period_ret: returns[i], member_cnt: members[i], rank: null };
    });
    groups.set(line.group_code, { name: line.name, color: line.color, points });
  }
  for (let i = 0; i < periods.length; i += 1) {
    const entries = [];
    for (const [code, group] of groups) {
      const point = group.points[i];
      if (point) entries.push({ code, point });
    }
    assignRanks(entries);
  }
  return groups;
}

/** 선택 기간의 누적수익. 그 기간에 값이 없으면 그 섹터는 빠진다 @returns {Map<string, any>} */
export function pointsAt(groups, index) {
  const out = new Map();
  for (const [code, group] of groups) {
    const point = group.points[index];
    if (point) out.set(code, point);
  }
  return out;
}

/**
 * 범프 차트가 읽는 /sectors/ranks 모양으로 바꾼다. 순위·수익률 자리에 누적 기준 값을 넣는다.
 * "표시: 상위 N"은 기간 기준과 같이 보이는 구간의 최고 순위로 거른다.
 *
 * @param {Map<string, any>} groups build 결과
 * @param {any[]} periods 앞 기간을 포함한 기간 행
 * @param {number} visible 뒤에서부터 그릴 기간 수
 * @param {{topN: number}} opts
 */
export function toRanks(groups, periods, visible, opts) {
  const skip = Math.max(0, periods.length - visible);
  const series = [];
  let hidden = 0;
  for (const [code, group] of groups) {
    const points = [];
    for (let i = skip; i < periods.length; i += 1) {
      const point = group.points[i];
      if (!point) continue;
      const previous = group.points[i - 1];
      points.push({ period_id: point.period_id, rank: point.rank,
                    rank_delta: previous ? previous.rank - point.rank : null,
                    return: point.ret, period_return: point.period_ret, member_cnt: point.member_cnt });
    }
    if (!points.length) continue;
    if (opts.topN && Math.min(...points.map((p) => p.rank)) > opts.topN) {
      hidden += 1;
      continue;
    }
    series.push({ group_code: code, name: group.name, color: group.color, points });
  }
  const shown = periods.slice(skip).map((p) => ({ period_id: p.period_id, end_date: p.end_date,
                                                  is_provisional: !p.is_closed, group_count: groups.size }));
  return { meta: {}, data: { periods: shown, series, others_count: hidden } };
}
