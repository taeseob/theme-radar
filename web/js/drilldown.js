// @ts-check
/** 드릴다운 패널 (docs/06 §5). "이 성과가 소수 종목에 집중되어 있는가"에 답하는 것이 목적이다. */
import { bp, escapeHtml, pct, ratio, signClass } from "./format.js";
import { seriesColor, token } from "./theme.js";

/** @type {Record<string, any>} */
const sparks = {};

function stat(key, value, note, title) {
  return `<div class="stat" ${title ? `title="${escapeHtml(title)}"` : ""}>
    <div class="k">${escapeHtml(key)}</div>
    <div class="v">${value}</div>
    ${note ? `<div class="k">${note}</div>` : ""}
  </div>`;
}

export function renderConcentration(container, summary) {
  const badges = summary.badges.map((b) => `<span class="badge">${escapeHtml(b)}</span>`).join(" ");
  container.innerHTML = [
    stat("상위 1종목 기여", ratio(summary.top1_contrib_share), "", "양(+)의 기여 총합 대비 비율이다"),
    stat("상위 3종목 기여", ratio(summary.top3_contrib_share), "", "양(+)의 기여 총합 대비 비율이다"),
    stat("유효 종목 수", summary.effective_n === null || summary.effective_n === undefined ? "—" : summary.effective_n.toFixed(2),
         `실질 / 전체 ${summary.member_cnt}종목`),
    stat("상승 종목", `${summary.up_cnt} <span class="k">/ ${summary.member_cnt}</span>`,
         `<span class="gauge"><span style="width:${((summary.up_ratio || 0) * 100).toFixed(1)}%"></span></span>`),
    stat("대형주 스프레드", `<span class="${signClass(summary.cap_weight_spread)}">${pct(summary.cap_weight_spread)}p</span>`,
         "시총가중 − 동일가중"),
    badges ? `<div class="stat badges">${badges}</div>` : "",
  ].join("");
}

function memberRow(member, max) {
  const contribution = member.contribution_in_group;
  const ret = member["return"];            // 응답의 필드 이름은 별칭 "return"이다 (docs/05 §5.1)
  const width = max ? Math.min(50, Math.abs(contribution || 0) / max * 50) : 0;
  const side = (contribution || 0) >= 0 ? `left:50%;width:${width}%` : `left:${50 - width}%;width:${width}%`;
  return `<div class="row" title="${escapeHtml(member.name)} (${escapeHtml(member.ticker)}) · 시장 비중 ${ratio(member.weight_in_universe, 2)}">
    <span class="name">${escapeHtml(member.name)} <span class="muted">${escapeHtml(member.ticker)}</span></span>
    <span class="bar-cell"><span class="zero"></span><span class="fill ${signClass(contribution)}-bg" style="${side}"></span></span>
    <span class="num">${ratio(member.weight_in_group, 1)}</span>
    <span class="num ${signClass(ret)}">${pct(ret)}</span>
    <span class="num hide-narrow">${bp(contribution)}</span>
  </div>`;
}

/**
 * @param {HTMLElement} container
 * @param {any} payload /sectors/{code}/breakdown 응답
 */
export function renderMembers(container, payload) {
  const { members, others, summary } = payload.data;
  if (!members.length) {
    container.innerHTML = '<p class="empty">구성 종목이 없습니다.</p>';
    return;
  }
  const max = Math.max(...members.map((m) => Math.abs(m.contribution_in_group || 0)), 1e-12);
  const head = `<div class="row head"><span class="name">종목</span><span class="bar-cell"></span>
    <span class="num">섹터 내 비중</span><span class="num">수익률</span><span class="num hide-narrow">기여</span></div>`;
  const rest = others.member_cnt
    ? `<div class="row muted"><span class="name">기타 ${others.member_cnt}종목</span>
       <span class="bar-cell"></span><span class="num">${ratio(others.weight_in_group, 1)}</span>
       <span class="num">—</span><span class="num hide-narrow">${bp(others.contribution_in_group)}</span></div>`
    : "";
  const total = `<p class="hint">막대 합계 = 섹터 수익률 <b class="${signClass(summary["return"])}">${pct(summary["return"])}</b>
    <span class="muted">(동일가중 ${pct(summary.return_equal)})</span></p>`;
  container.innerHTML = head + members.map((m) => memberRow(m, max)).join("") + rest + total;
}

function sparkline(id, points, field, { inverse = false, percent = false } = {}) {
  const element = document.getElementById(id);
  if (!element) return;
  const chart = sparks[id] && sparks[id].getDom() === element ? sparks[id] : (sparks[id] = window.echarts.init(element));
  const values = points.map((p) => {
    const value = p[field];
    return value === null || value === undefined ? null : (percent ? value * 100 : value);
  });
  const label = (value) => (value === null || value === undefined ? "—" : percent ? `${value.toFixed(1)}%` : String(value));
  chart.setOption({
    backgroundColor: "transparent",
    // 스파크라인은 모양만 본다. 눈금 대신 끝 값과 툴팁으로 크기를 읽는다
    grid: { left: 4, right: 46, top: 10, bottom: 6 },
    xAxis: { type: "category", data: points.map((p) => p.period_id), show: false, boundaryGap: false },
    yAxis: { type: "value", inverse, scale: true, show: false },
    tooltip: { trigger: "axis", confine: true, borderColor: token("--border"), backgroundColor: token("--surface"),
               textStyle: { color: token("--ink"), fontSize: 11 }, axisPointer: { lineStyle: { color: token("--line") } },
               formatter: (items) => `${items[0].name}<br>${label(items[0].value)}` },
    series: [{ type: "line", data: values, connectNulls: false, symbol: "circle", symbolSize: 4,
               lineStyle: { width: 1.5, color: token("--accent") }, itemStyle: { color: token("--accent") },
               endLabel: { show: true, fontSize: 11, color: token("--ink-2"), distance: 5,
                           formatter: (p) => (p.value === null ? "" : label(p.value)) } }],
  }, { notMerge: true });
  chart.resize();
}

/** 수익률·순위·상위1 기여 스파크라인 (docs/06 §5.3) */
export function renderSparklines(history) {
  sparkline("spark-return", history.data, "return", { percent: true });
  sparkline("spark-rank", history.data, "rank", { inverse: true });
  sparkline("spark-top1", history.data, "top1_contrib_share", { percent: true });
}

export function title(meta, periodId) {
  const color = seriesColor(meta.color);
  return `<span class="dot" style="background:${color}"></span> ${escapeHtml(meta.name || meta.group_code)}
    <span class="muted">· ${escapeHtml(periodId)} 상세</span>`;
}

export function resize() {
  for (const chart of Object.values(sparks)) chart.resize();
}

export function repaint(history) {
  if (history) renderSparklines(history);
}
