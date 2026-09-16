// @ts-check
/** 범프 차트 (docs/06 §3). 순위 모드는 y축을 뒤집고, 수익률 모드는 일반 선 차트다. */
import { bp, escapeHtml, pct, periodLabels } from "./format.js";
import { seriesColor, token } from "./theme.js";

const GRID = { left: 52, right: 156, top: 16, bottom: 68 };
const LABEL_GAP = 13;                  // 끝단 라벨 한 줄이 차지하는 높이

/** @type {any} */
let chart = null;
/** @type {{payload: any, mode: string, calendar: Map<string, any>, onPick: Function}|null} */
let last = null;

/** 잠정 구간은 실선 계열을 끊고 점선 계열로 잇는다. ECharts에는 구간별 선 스타일이 없다 */
function split(points, periods, mode) {
  const index = new Map(periods.map((p, i) => [p.period_id, i]));
  const firstProvisional = periods.findIndex((p) => p.is_provisional);
  const solid = new Array(periods.length).fill(null);
  const dashed = new Array(periods.length).fill(null);
  for (const point of points) {
    const i = index.get(point.period_id);
    if (i === undefined) continue;
    const value = mode === "rank" ? point.rank : (point.return === null || point.return === undefined ? null : point.return * 100);
    if (value === null) continue;                  // 결측 기간은 점을 찍지 않고 선을 끊는다
    const provisional = firstProvisional >= 0 && i >= firstProvisional;
    const item = { value, point, provisional };
    if (provisional) dashed[i] = item;
    else solid[i] = item;
  }
  // 확정 → 잠정 이음매는 양쪽에 같은 점을 둬야 선이 이어진다
  if (firstProvisional > 0 && dashed[firstProvisional] && solid[firstProvisional - 1]) {
    dashed[firstProvisional - 1] = { ...solid[firstProvisional - 1], joint: true };
  }
  return { solid, dashed, hasDashed: dashed.some(Boolean) };
}

/** 잠정 기간은 속 빈 원, 수익률 부호는 테두리 종류로 구분한다 (docs/06 §3.1, §3.4) */
function decorate(color, surface) {
  return (item) => {
    if (!item) return null;
    const hollow = item.provisional && !item.joint;
    const positive = (item.point.return ?? 0) >= 0;
    // 선 계열의 기본 심볼은 emptyCircle이라 채우려면 circle을 직접 지정해야 한다.
    // 채운 점의 테두리는 배경색이다. 겹친 점끼리 떨어져 보인다
    return { ...item, symbol: hollow ? "emptyCircle" : "circle",
             itemStyle: { color, borderColor: hollow ? color : surface, borderWidth: 2,
                          borderType: positive ? "solid" : "dashed" } };
  };
}

/**
 * 끝단 라벨은 필수 식별 수단이라(docs/06 §8) 자리가 부족하면 섞어 쓰기보다 생략한다 (docs/06 §3.1).
 * 각 계열의 마지막 값을 픽셀로 바꿔, 위에서부터 최소 간격을 지키는 것만 고른다.
 */
function fittingLabels(payload, mode, bounds, plotHeight) {
  const span = bounds.max - bounds.min || 1;
  const placed = [];
  for (const line of payload.data.series) {
    const point = [...line.points].reverse().find((p) => (mode === "rank" ? p.rank : p.return) !== null);
    if (!point) continue;
    const value = mode === "rank" ? point.rank : point.return * 100;
    const ratio = (value - bounds.min) / span;
    placed.push({ code: line.group_code, y: (mode === "rank" ? ratio : 1 - ratio) * plotHeight });
  }
  placed.sort((a, b) => a.y - b.y);
  const kept = new Set();
  let previous = -Infinity;
  for (const item of placed) {
    if (item.y - previous < LABEL_GAP) continue;
    kept.add(item.code);
    previous = item.y;
  }
  return kept;
}

function buildSeries(payload, mode, labelled) {
  const color0 = token("--muted");
  const surface = token("--surface") || "#fcfcfb";
  const series = [];
  for (const line of payload.data.series) {
    const color = seriesColor(line.color) || color0;
    const { solid, dashed, hasDashed } = split(line.points, payload.data.periods, mode);
    const style = decorate(color, surface);
    const showLabel = labelled.has(line.group_code);
    const common = {
      name: line.name, type: "line", connectNulls: false, symbol: "circle", symbolSize: 10,
      itemStyle: { color }, emphasis: { focus: "series", lineStyle: { width: 3.5 } },
      blur: { lineStyle: { opacity: 0.2 }, itemStyle: { opacity: 0.2 }, endLabel: { opacity: 0.2 } },
    };
    const endLabel = { show: showLabel, distance: 6, fontSize: 11, color: token("--ink-2"),
                       formatter: (params) => params.seriesName };
    const labelLayout = { moveOverlap: "shiftY", hideOverlap: true };
    series.push({ ...common, id: line.group_code, data: solid.map(style), lineStyle: { width: 2, color },
                  endLabel: hasDashed ? { show: false } : endLabel, labelLayout });
    if (hasDashed) {
      series.push({ ...common, id: `${line.group_code}~p`, data: dashed.map(style),
                    lineStyle: { width: 2, color, type: "dashed" }, legendHoverLink: true,
                    endLabel, labelLayout, z: 3 });
    }
  }
  return series;
}

function tooltipHtml(state, params) {
  const item = params.data;
  if (!item || !item.point) return "";
  const point = item.point;
  const period = state.payload.data.periods.find((p) => p.period_id === point.period_id);
  const cal = state.calendar.get(point.period_id);
  const universeReturn = period?.universe_return;
  const delta = point.rank_delta;
  const rows = [
    ["순위", `<b>${point.rank}위</b>`, delta === null || delta === undefined ? ""
      : `(전 기간 ${point.rank + delta}위, ${delta > 0 ? `▲${delta}` : delta < 0 ? `▼${-delta}` : "–"})`],
    ["수익률", `<b>${pct(point.return)}</b>`, ""],
    ["시장 비중", point.base_weight === null || point.base_weight === undefined ? "—" : `${(point.base_weight * 100).toFixed(2)}%`, ""],
    ["시장 기여", bp(point.contribution),
      universeReturn && Math.abs(universeReturn) >= 0.0005 && point.contribution !== null && point.contribution !== undefined
        ? `(시장 ${bp(universeReturn)}의 ${Math.round(point.contribution / universeReturn * 100)}%)` : ""],
    ["구성종목", `${point.member_cnt}종목`, ""],
    ["상위1 기여", point.top1_contrib_share === null || point.top1_contrib_share === undefined
      ? "—" : `${Math.round(point.top1_contrib_share * 100)}%`, ""],
  ];
  const span = cal ? `${cal.cal_start} ~ ${cal.cal_end}` : "";
  const note = period?.is_provisional ? "기간 종료 전 최신 거래일 기준(잠정)" : "";
  return [
    `<b>${escapeHtml(params.seriesName)}</b> · ${point.period_id}${period?.is_provisional ? " (잠정)" : ""}`,
    span ? `<span style="opacity:.7">${span}</span>` : "",
    '<div style="height:1px;background:currentColor;opacity:.15;margin:5px 0"></div>',
    ...rows.map(([k, v, extra]) =>
      `<span style="display:inline-block;min-width:74px;opacity:.7">${k}</span>${v}${extra ? ` <span style="opacity:.7">${extra}</span>` : ""}`),
    `<div style="margin-top:5px;opacity:.7">기준일 ${cal?.base_date ?? "—"} → ${period?.end_date ?? "—"}${note ? ` · ${note}` : ""}</div>`,
  ].filter(Boolean).join("<br>");
}

function options(state, plotHeight) {
  const { payload, mode } = state;
  const labels = periodLabels(payload.data.periods.map((p) => p.period_id))
    .map((label, i) => (payload.data.periods[i].is_provisional ? `${label}\n(잠정)` : label));
  const maxRank = Math.max(1, ...payload.data.series.flatMap((s) => s.points.map((p) => p.rank)));
  // 두 모드 모두 축 범위를 직접 정한다. 끝단 라벨 자리를 계산하려면 범위를 알아야 한다
  const returns = payload.data.series.flatMap((s) => s.points.map((p) => p.return)).filter((v) => v !== null);
  const pad = (Math.max(...returns) - Math.min(...returns)) * 0.06 || 0.01;
  const bounds = mode === "rank" ? { min: 1, max: maxRank }
    : { min: (Math.min(...returns) - pad) * 100, max: (Math.max(...returns) + pad) * 100 };
  const labelled = fittingLabels(payload, mode, bounds, plotHeight);
  const axisLabel = { color: token("--muted"), fontSize: 11 };
  return {
    backgroundColor: "transparent",
    animationDuration: 200,
    grid: { ...GRID },
    tooltip: { trigger: "item", confine: true, borderColor: token("--border"), backgroundColor: token("--surface"),
               textStyle: { color: token("--ink"), fontSize: 12 }, extraCssText: "line-height:1.55",
               formatter: (params) => tooltipHtml(state, params) },
    legend: { type: "scroll", bottom: 0, itemWidth: 12, itemHeight: 8, textStyle: { color: token("--ink-2"), fontSize: 11 },
              pageTextStyle: { color: token("--muted") }, pageIconColor: token("--line"),
              data: payload.data.series.map((s) => s.name) },
    xAxis: { type: "category", data: labels, boundaryGap: false,
             axisLine: { lineStyle: { color: token("--line") } }, axisTick: { show: false },
             axisLabel: { ...axisLabel, interval: labels.length > 30 ? "auto" : 0 } },
    yAxis: mode === "rank"
      ? { type: "value", inverse: true, min: bounds.min, max: bounds.max, minInterval: 1,
          interval: maxRank > 12 ? 5 : 1, axisLabel: { ...axisLabel, formatter: (v) => `${v}위` },
          splitLine: { lineStyle: { color: token("--grid") } } }
      : { type: "value", min: bounds.min, max: bounds.max, splitNumber: 5,
          axisLabel: { ...axisLabel, formatter: (v) => `${v.toFixed(1)}%` },
          splitLine: { lineStyle: { color: token("--grid") } } },
    dataZoom: [{ type: "inside", filterMode: "none" },
               { type: "slider", height: 14, bottom: 30, borderColor: "transparent",
                 backgroundColor: "transparent", fillerColor: token("--grid"),
                 dataBackground: { lineStyle: { color: token("--line") }, areaStyle: { color: "transparent" } },
                 selectedDataBackground: { lineStyle: { color: token("--line") }, areaStyle: { color: "transparent" } },
                 handleStyle: { color: token("--surface"), borderColor: token("--line") },
                 moveHandleStyle: { color: token("--line") }, textStyle: { color: token("--muted"), fontSize: 10 } }],
    series: buildSeries(payload, mode, labelled),
  };
}

/**
 * @param {HTMLElement} container
 * @param {any} payload /sectors/ranks 응답
 * @param {{mode: string, calendar: Map<string, any>, onPick: (groupCode: string, periodId?: string) => void}} opts
 */
export function render(container, payload, opts) {
  last = { payload, mode: opts.mode, calendar: opts.calendar, onPick: opts.onPick };
  if (!chart || chart.getDom() !== container) {
    chart?.dispose();
    chart = window.echarts.init(container, null, { renderer: "canvas" });
    chart.on("click", (params) => {
      const code = String(params.seriesId || "").replace(/~p$/, "");
      if (code) last?.onPick(code, params.data?.point?.period_id);
    });
    container.addEventListener("dblclick", () => chart.dispatchAction({ type: "dataZoom", start: 0, end: 100 }));
    window.addEventListener("resize", () => chart?.resize());
  }
  chart.setOption(options(last, plotHeight(container)), { notMerge: true });
  chart.resize();
  return chart;
}

/** 테마 전환처럼 데이터는 그대로고 색만 바뀔 때 */
function plotHeight(container) {
  return Math.max(60, container.clientHeight - GRID.top - GRID.bottom);
}

export function repaint() {
  if (chart && last) chart.setOption(options(last, plotHeight(chart.getDom())), { notMerge: true });
}

export function showLoading(container) {
  if (chart && chart.getDom() === container) chart.showLoading("default", {
    text: "", color: token("--accent"), maskColor: "transparent", spinnerRadius: 12,
  });
}

export function hideLoading() {
  chart?.hideLoading();
}

export function dispose() {
  chart?.dispose();
  chart = null;
  last = null;
}

/** 차트 데이터의 표 대체 표현 (docs/06 §8). CSV 내보내기와 같은 데이터다 */
export function tableHtml(payload, mode) {
  const periods = payload.data.periods;
  const head = periods.map((p) => `<th scope="col">${p.period_id}${p.is_provisional ? " (잠정)" : ""}</th>`).join("");
  const rows = payload.data.series.map((line) => {
    const byPeriod = new Map(line.points.map((p) => [p.period_id, p]));
    const cells = periods.map((p) => {
      const point = byPeriod.get(p.period_id);
      if (!point) return "<td>—</td>";
      return `<td>${mode === "rank" ? `${point.rank}위` : pct(point.return)}</td>`;
    }).join("");
    return `<tr><th scope="row">${escapeHtml(line.name)}</th>${cells}</tr>`;
  }).join("");
  return `<table><caption class="muted">범프 차트와 같은 데이터 (${mode === "rank" ? "순위" : "수익률"})</caption>`
    + `<thead><tr><th scope="col">섹터</th>${head}</tr></thead><tbody>${rows}</tbody></table>`;
}
