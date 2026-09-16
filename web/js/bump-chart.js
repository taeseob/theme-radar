// @ts-check
/** 범프 차트 (docs/06 §3). 순위 모드는 y축을 뒤집고, 수익률 모드는 일반 선 차트다. */
import { bp, escapeHtml, pct, periodLabels } from "./format.js";
import { seriesColor, token } from "./theme.js";

const GRID = { left: 52, right: 156, top: 16, bottom: 68 };
const LABEL_GAP = 13;                  // 끝단 라벨 한 줄이 차지하는 높이
const TIP_GAP = 16;                    // 툴팁 상자와 마우스 포인터 사이 간격
const SYMBOL_HIT = 8;                  // 이 거리 안이면 선이 아니라 점을 눌렀다고 본다

/** @type {any} */
let chart = null;
/** @type {{payload: any, mode: string, calendar: Map<string, any>, onPick: Function}|null} */
let last = null;
/** @type {HTMLElement|null} */
let tip = null;
let pointer = [0, 0];                  // 마지막 마우스 위치 (차트 좌표계)

/**
 * 기간 축에 맞춘 값 배열. 결측 기간은 null로 둬 선을 끊는다 (보간하지 않는다).
 * 수익률 부호는 점 테두리 종류로 구분한다 (docs/06 §3.4).
 */
function values(line, periods, mode, color, surface) {
  const index = new Map(periods.map((p, i) => [p.period_id, i]));
  const data = new Array(periods.length).fill(null);
  for (const point of line.points) {
    const i = index.get(point.period_id);
    if (i === undefined) continue;
    const value = mode === "rank" ? point.rank : (point.return === null || point.return === undefined ? null : point.return * 100);
    if (value === null) continue;
    const positive = (point.return ?? 0) >= 0;
    // 채운 점의 테두리는 배경색이다. 겹친 점끼리 떨어져 보인다
    data[i] = { value, point,
                itemStyle: { color, borderColor: surface, borderWidth: 2, borderType: positive ? "solid" : "dashed" } };
  }
  return data;
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
  return payload.data.series.map((line) => {
    const color = seriesColor(line.color) || color0;
    return {
      id: line.group_code, name: line.name, type: "line", connectNulls: false,
      symbol: "circle", symbolSize: 10, itemStyle: { color }, lineStyle: { width: 2, color },
      triggerLineEvent: true,            // 선 위에서도 마우스 이벤트를 받는다 (툴팁·클릭)
      emphasis: { focus: "series", lineStyle: { width: 3.5 } },
      blur: { lineStyle: { opacity: 0.2 }, itemStyle: { opacity: 0.2 }, endLabel: { opacity: 0.2 } },
      endLabel: { show: labelled.has(line.group_code), distance: 6, fontSize: 11, color: token("--ink-2"),
                  formatter: (params) => params.seriesName },
      labelLayout: { moveOverlap: "shiftY", hideOverlap: true },
      data: values(line, payload.data.periods, mode, color, surface),
    };
  });
}

function tooltipHtml(state, name, point) {
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
  const divider = '<div style="height:1px;background:currentColor;opacity:.15;margin:5px 0"></div>';
  return [
    `<b>${escapeHtml(name)}</b> · ${point.period_id}`,
    span ? `<span style="opacity:.7">${span}</span>` : "",
    divider,
    ...rows.map(([k, v, extra]) =>
      `<span style="display:inline-block;min-width:74px;opacity:.7">${k}</span>${v}${extra ? ` <span style="opacity:.7">${extra}</span>` : ""}`),
    `<div style="margin-top:5px;opacity:.7">기준일 ${cal?.base_date ?? "—"} → ${period?.end_date ?? "—"}</div>`,
  ].filter(Boolean).join("<br>");
}

// ── 툴팁 ──────────────────────────────────────────────────────────────────────
// ECharts 툴팁은 데이터 점에만 붙어 선 위에서는 뜨지 않는다. 상자를 직접 그려 점과 선을 같게 다룬다

function showTip(html) {
  const container = chart.getDom();
  if (!tip || tip.parentElement !== container) {
    tip = document.createElement("div");
    tip.className = "chart-tip";
    container.appendChild(tip);
  }
  tip.innerHTML = html;
  tip.hidden = false;
  // 기본 자리는 마우스 포인터의 왼쪽이다. 왼쪽이 좁으면 오른쪽으로 넘긴다
  const [x, y] = pointer;
  const left = x - tip.offsetWidth - TIP_GAP;
  tip.style.left = `${Math.max(0, left >= 0 ? left : Math.min(x + TIP_GAP, container.clientWidth - tip.offsetWidth))}px`;
  tip.style.top = `${Math.min(Math.max(y - tip.offsetHeight / 2, 0), Math.max(0, container.clientHeight - tip.offsetHeight))}px`;
}

function hideTip() {
  if (tip) tip.hidden = true;
}

/** 선 위 좌표에서 가장 가까운 기간의 점. 그 기간에 값이 없으면 null */
function nearestPoint(seriesIndex, x, y) {
  const periods = last.payload.data.periods;
  const at = chart.convertFromPixel({ seriesIndex }, [x, y]);
  const index = Math.round(at ? at[0] : NaN);
  if (!(index >= 0) || index >= periods.length) return null;
  const point = last.payload.data.series[seriesIndex]?.points.find((p) => p.period_id === periods[index].period_id);
  if (!point) return null;
  const value = last.mode === "rank" ? point.rank : point.return;
  return value === null || value === undefined ? null : { point, index, value: last.mode === "rank" ? value : value * 100 };
}

/**
 * 점을 눌렀으면 그 기간, 선을 눌렀으면 기간 없이 섹터만 고른다 (docs/06 §3.2).
 * 선이 점 위를 덮어 ECharts는 점 클릭을 따로 알려 주지 않는다. 마우스와 점 사이 거리로 가른다.
 */
function clickedPeriod(params) {
  if (params.data?.point) return params.data.point.period_id;
  const near = nearestPoint(params.seriesIndex, pointer[0], pointer[1]);
  if (!near) return undefined;
  const pixel = chart.convertToPixel({ seriesIndex: params.seriesIndex }, [near.index, near.value]);
  return Math.hypot(pixel[0] - pointer[0], pixel[1] - pointer[1]) <= SYMBOL_HIT ? near.point.period_id : undefined;
}

function onMove(params) {
  if (params.componentType !== "series" || !last || !params.event) return;
  pointer = [params.event.offsetX, params.event.offsetY];
  // 점 위에서는 그 점을, 선 위에서는 마우스와 같은 기간의 점을 보여 준다
  const point = params.data?.point || nearestPoint(params.seriesIndex, pointer[0], pointer[1])?.point;
  if (point) showTip(tooltipHtml(last, params.seriesName, point));
  else hideTip();
}

/** 선 위 이벤트에는 seriesId가 없다. 계열 순서는 응답과 같아 자리로 찾는다 */
function seriesCode(params) {
  return String(params.seriesId || last?.payload.data.series[params.seriesIndex]?.group_code || "");
}

function options(state, plotHeight) {
  const { payload, mode } = state;
  const labels = periodLabels(payload.data.periods.map((p) => p.period_id));
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
    tooltip: { show: false },            // 툴팁 상자는 직접 그린다 (showTip)
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
      const code = seriesCode(params);
      if (code) last?.onPick(code, clickedPeriod(params));
    });
    chart.on("mousemove", onMove);
    // 선이나 점에서 벗어나면 거둔다. 다른 계열로 옮겨 가는 경우도 같은 mousemove 안에서 mouseout → mousemove 순으로 온다
    chart.on("mouseout", hideTip);
    chart.getZr().on("globalout", hideTip);
    container.addEventListener("dblclick", () => chart.dispatchAction({ type: "dataZoom", start: 0, end: 100 }));
  }
  hideTip();
  chart.setOption(options(last, plotHeight(container)), { notMerge: true });
  chart.resize();
  return chart;
}

function plotHeight(container) {
  return Math.max(60, container.clientHeight - GRID.top - GRID.bottom);
}

/** 테마 전환이나 높이 조절처럼 데이터는 그대로고 그리는 자리만 바뀔 때 */
export function repaint() {
  if (!chart || !last || !chart.getDom().clientHeight) return;   // 표로 보는 중이면 자리가 없다
  hideTip();
  chart.resize();
  chart.setOption(options(last, plotHeight(chart.getDom())), { notMerge: true });
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
  tip = null;
}

/** 차트 데이터의 표 대체 표현 (docs/06 §8). CSV 내보내기와 같은 데이터다 */
export function tableHtml(payload, mode) {
  const periods = payload.data.periods;
  const head = periods.map((p) => `<th scope="col">${p.period_id}</th>`).join("");
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
