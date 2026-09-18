// @ts-check
/**
 * 섹터 시총 이동평균 (docs/03 §14.3~14.4). 저장하지 않고 화면이 기간 말 시총으로 계산한다.
 * 상승률(이동평균의 기간 대비 변화율), 그 상승률의 순위, 이격도(기간 말 시총 ÷ 이동평균)를 여기서 만든다.
 */

/**
 * N기간 단순 이동평균. 창 안에 값이 빈 기간이 있으면 그 자리는 비운다 (보간하지 않는다).
 * @param {(number|null)[]} values @param {number} n
 */
export function movingAverage(values, n) {
  return values.map((_, i) => {
    if (i < n - 1) return null;
    const window = values.slice(i - n + 1, i + 1);
    return window.some((v) => v === null) ? null : window.reduce((sum, v) => sum + /** @type {number} */ (v), 0) / n;
  });
}

/** 경쟁 순위. 동점이면 같은 순위를 주고 다음 순위를 건너뛴다 (1, 2, 2, 4) (docs/03 §6) */
function assignRanks(entries) {
  entries.sort((a, b) => b.point.ret - a.point.ret || (a.code < b.code ? -1 : 1));
  let previous = null;
  let previousRank = 0;
  entries.forEach((entry, position) => {
    if (previous === null || entry.point.ret !== previous) {
      previous = entry.point.ret;
      previousRank = position + 1;
    }
    entry.point.rank = previousRank;
  });
}

/**
 * 섹터별 기간 말 시총으로 이동평균·상승률·이격도를 구하고, 기간마다 상승률 순위를 매긴다.
 *
 * @param {any} payload /sectors/market-caps 응답
 * @param {any[]} periods 앞 기간을 포함한 기간 행 (period_seq 오름차순). 값은 이 배열에 맞춰 늘어선다
 * @param {number} n 이동평균 기간 수
 * @returns {Map<string, {name: string, color: string|null, points: (any|null)[]}>}
 */
export function build(payload, periods, n) {
  const index = new Map(periods.map((p, i) => [p.period_id, i]));
  const groups = new Map();
  for (const line of payload.data) {
    const closes = /** @type {(number|null)[]} */ (new Array(periods.length).fill(null));
    const members = new Array(periods.length).fill(null);
    for (const point of line.points) {
      const i = index.get(point.period_id);
      if (i === undefined) continue;
      closes[i] = point.close;
      members[i] = point.member_cnt;
    }
    const average = movingAverage(closes, n);
    const points = periods.map((p, i) => {
      const value = average[i];
      if (value === null) return null;                    // 창이 다 차지 않았거나 빈 기간이 끼어 있다
      const previous = average[i - 1];
      const close = /** @type {number} */ (closes[i]);
      return { period_id: p.period_id, close, ma: value, member_cnt: members[i], disparity: close / value,
               ret: previous === null || previous === undefined ? null : value / previous - 1, rank: null };
    });
    groups.set(line.group_code, { name: line.name, color: line.color, points });
  }
  for (let i = 0; i < periods.length; i += 1) {
    const entries = [];
    for (const [code, group] of groups) {
      const point = group.points[i];
      if (point && point.ret !== null) entries.push({ code, point });
    }
    assignRanks(entries);
  }
  return groups;
}

/** 선택 기간의 이격도. 그 기간에 이동평균이 없으면 그 섹터는 빠진다 @returns {Map<string, any>} */
export function pointsAt(groups, index) {
  const out = new Map();
  for (const [code, group] of groups) {
    const point = group.points[index];
    if (point) out.set(code, point);
  }
  return out;
}

/**
 * 범프 차트가 읽는 /sectors/ranks 모양으로 바꾼다. 순위·상승률 자리에 이동평균 기준 값을 넣어
 * 차트는 두 기준을 같게 다룬다.
 *
 * @param {Map<string, any>} groups build 결과
 * @param {any[]} periods 앞 기간을 포함한 기간 행
 * @param {number} visible 뒤에서부터 그릴 기간 수
 * @param {{topN: number, above: boolean, at: number}} opts
 *   at은 "이동평균 위" 판정에 쓸 기간 자리(선택 기간)다
 */
export function toRanks(groups, periods, visible, opts) {
  const skip = Math.max(0, periods.length - visible);
  const series = [];
  let hidden = 0;
  for (const [code, group] of groups) {
    const points = [];
    for (let i = skip; i < periods.length; i += 1) {
      const point = group.points[i];
      if (!point || point.ret === null) continue;
      const previous = group.points[i - 1];
      points.push({ period_id: point.period_id, rank: point.rank,
                    rank_delta: previous && previous.rank !== null ? previous.rank - point.rank : null,
                    return: point.ret, ma: point.ma, close: point.close, disparity: point.disparity,
                    member_cnt: point.member_cnt });
    }
    if (!points.length) continue;
    const best = Math.min(...points.map((p) => p.rank));
    const at = group.points[opts.at];
    if ((opts.topN && best > opts.topN) || (opts.above && !(at && at.disparity > 1))) {
      hidden += 1;
      continue;
    }
    series.push({ group_code: code, name: group.name, color: group.color, points });
  }
  const shown = periods.slice(skip).map((p) => ({ period_id: p.period_id, end_date: p.end_date,
                                                  is_provisional: !p.is_closed, group_count: groups.size }));
  return { meta: {}, data: { periods: shown, series, others_count: hidden } };
}
