// @ts-check
/**
 * 섹터 시가총액 차트 (docs/06 §5.4). 선은 기간 말 값을 잇고, 캔들은 일별 값으로 만든 기간 시가·고가·저가·종가다.
 * 이동평균은 DB에 두지 않고 여기서 기간 말 값으로 계산한다.
 */
import { cap, capTick, escapeHtml, pct, periodLabels } from "./format.js";
import { seriesColor, token } from "./theme.js";

/** @type {any} */
let chart = null;
/** @type {any} */
let last = null;

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

function tooltipHtml(state, index) {
  const { name, currency, kind, ma } = state;
  const point = state.points[index];
  const row = state.periods[index];
  const key = (label) => `<span style="display:inline-block;min-width:74px;opacity:.7">${label}</span>`;
  const lines = [`<b>${escapeHtml(name)}</b> · ${escapeHtml(row.period_id)}`,
                 `<span style="opacity:.7">${row.cal_start} ~ ${row.cal_end}</span>`,
                 '<div style="height:1px;background:currentColor;opacity:.15;margin:5px 0"></div>'];
  if (!point) {
    lines.push(`${key("시가총액")}—`);
  } else {
    const change = point.open ? point.close / point.open - 1 : null;
    if (kind === "candle") {
      lines.push(`${key("시가")}${cap(point.open, currency)}`, `${key("고가")}${cap(point.high, currency)}`,
                 `${key("저가")}${cap(point.low, currency)}`);
    }
    lines.push(`${key(kind === "candle" ? "종가" : "시가총액")}<b>${cap(point.close, currency)}</b>`
               + (change === null ? "" : ` <span style="opacity:.7">(${pct(change)})</span>`));
  }
  if (ma) lines.push(`${key(`${ma.length}기간 평균`)}${cap(ma.values[index], currency)}`);
  if (point) {
    lines.push(`${key("구성종목")}${point.member_cnt}종목`,
               `<div style="margin-top:5px;opacity:.7">기준일 ${point.base_date} → ${point.end_date}</div>`);
  }
  return lines.join("<br>");
}

function options(state) {
  const { periods, points, kind, ma, selected } = state;
  const axisLabel = { color: token("--muted"), fontSize: 11 };
  const color = seriesColor(state.color);
  const up = token("--up");
  const down = token("--down");
  const selectedIndex = periods.findIndex((p) => p.period_id === selected);
  // 드릴다운이 보고 있는 기간을 세로 실선으로 짚는다
  const marker = selectedIndex < 0 ? undefined : {
    silent: true, symbol: "none", label: { show: false },
    lineStyle: { color: token("--muted"), type: "solid", width: 1 }, data: [{ xAxis: selectedIndex }],
  };
  const main = kind === "candle"
    ? { type: "candlestick", name: "시가총액", barMaxWidth: 14, markLine: marker,
        // ECharts 캔들 값 순서는 [시가, 종가, 저가, 고가]다
        data: points.map((p) => (p ? [p.open, p.close, p.low, p.high] : "-")),
        itemStyle: { color: up, color0: down, borderColor: up, borderColor0: down } }
    : { type: "line", name: "시가총액", connectNulls: false, showSymbol: false, symbol: "circle", symbolSize: 8,
        markLine: marker, data: points.map((p) => (p ? p.close : null)),
        lineStyle: { width: 2, color }, itemStyle: { color, borderColor: token("--surface"), borderWidth: 2 } };
  const series = [main];
  if (ma) {
    series.push({ type: "line", name: `${ma.length}기간 이동평균`, connectNulls: false, showSymbol: false, symbol: "circle",
                  symbolSize: 8, data: ma.values, lineStyle: { width: 2, color: token("--ink-2") },
                  itemStyle: { color: token("--ink-2"), borderColor: token("--surface"), borderWidth: 2 } });
  }
  return {
    backgroundColor: "transparent",
    animationDuration: 200,
    grid: { left: 70, right: 16, top: ma ? 30 : 12, bottom: 40 },
    // 계열이 둘이면 범례를 둔다. 하나면 소제목이 이름을 대신한다
    legend: { show: Boolean(ma), top: 0, right: 8, itemWidth: 14, itemHeight: 8,
              textStyle: { color: token("--ink-2"), fontSize: 11 } },
    tooltip: { trigger: "axis", confine: true, borderColor: token("--border"), backgroundColor: token("--surface"),
               textStyle: { color: token("--ink"), fontSize: 12 }, extraCssText: "box-shadow: 0 2px 10px rgba(0,0,0,.12);",
               axisPointer: { type: "line", lineStyle: { color: token("--line") } },
               formatter: (items) => tooltipHtml(state, items[0].dataIndex) },
    xAxis: { type: "category", data: periodLabels(periods.map((p) => p.period_id)), boundaryGap: kind === "candle",
             axisLine: { lineStyle: { color: token("--line") } }, axisTick: { show: false },
             axisLabel: { ...axisLabel, interval: periods.length > 30 ? "auto" : 0 } },
    // 시총은 0에서 멀리 떨어져 있어 0부터 그리면 움직임이 보이지 않는다
    yAxis: { type: "value", scale: true, splitNumber: 4,
             axisLabel: { ...axisLabel, formatter: (v) => capTick(v, state.currency) },
             splitLine: { lineStyle: { color: token("--grid") } } },
    series,
  };
}

/**
 * @param {HTMLElement} container
 * @param {any} payload /sectors/{code}/market-cap 응답. 이동평균에 쓸 앞 기간이 붙어 있을 수 있다
 * @param {{periods: any[], visible: number, kind: string, maLength: number, selected: string, color?: string}} opts
 *   periods는 앞 기간을 포함한 /meta/periods 행이고, 그중 마지막 visible개만 그린다
 */
export function render(container, payload, opts) {
  const byPeriod = new Map(payload.data.map((p) => [p.period_id, p]));
  const all = opts.periods.map((row) => byPeriod.get(row.period_id) || null);
  const skip = Math.max(0, opts.periods.length - opts.visible);
  const ma = opts.maLength > 1
    ? { length: opts.maLength, values: movingAverage(all.map((p) => (p ? p.close : null)), opts.maLength).slice(skip) }
    : null;
  last = { periods: opts.periods.slice(skip), points: all.slice(skip), kind: opts.kind, ma, selected: opts.selected,
           color: opts.color, name: payload.meta.name || payload.meta.group_code, currency: payload.meta.currency };
  if (!chart || chart.getDom() !== container) {
    chart?.dispose();
    container.innerHTML = "";
    chart = window.echarts.init(container, null, { renderer: "canvas" });
  }
  chart.setOption(options(last), { notMerge: true });
  chart.resize();
}

/** 차트를 거두고 안내 문구를 둔다 */
export function message(container, html) {
  chart?.dispose();
  chart = null;
  last = null;
  container.innerHTML = html;
}

export function resize() {
  chart?.resize();
}

/** 테마 전환처럼 데이터는 그대로고 색만 바뀔 때 */
export function repaint() {
  if (chart && last) chart.setOption(options(last), { notMerge: true });
}
