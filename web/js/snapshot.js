// @ts-check
/** 기간 스냅샷 패널 (docs/06 §4). 막대는 0을 중심으로 양방향이다. */
import { bp, escapeHtml, pct, rankDelta, ratio, signClass } from "./format.js";
import { seriesColor } from "./theme.js";

const UNMAPPED_MIN_WEIGHT = 0.001;   // 시총 비중 0.1% 미만인 미매핑 행은 감춘다

function bar(value, max) {
  if (!value || !max) return '<span class="zero"></span>';
  const width = Math.min(50, Math.abs(value) / max * 50);
  const side = value > 0 ? `left:50%;width:${width}%` : `left:${50 - width}%;width:${width}%`;
  return `<span class="zero"></span><span class="fill ${signClass(value)}-bg" style="${side}"></span>`;
}

function row(sector, max, selected) {
  const unmapped = sector.group_code === "UNMAPPED";
  const ret = sector["return"];            // 응답의 필드 이름은 별칭 "return"이다 (docs/05 §3.2)
  const delta = rankDelta(sector.rank_delta);
  const badges = sector.badges.map((b) => `<span class="badge">${escapeHtml(b)}</span>`).join("");
  // 시장 수익률 대비 비율은 보조 텍스트다. 시장과 방향이 반대면 음수 비율이 되어 읽히지 않으므로 뺀다
  const share = (sector.contribution_share || 0) >= 0.005
    ? ` <span class="muted">${ratio(sector.contribution_share)}</span>` : "";
  return `<div class="row ${unmapped ? "unmapped" : "clickable"}${selected ? " selected" : ""}"
       ${unmapped ? "" : `data-group="${escapeHtml(sector.group_code)}" role="button" tabindex="0"`}
       title="${escapeHtml(sector.name)} · 시총 비중 ${ratio(sector.base_weight, 2)} · ${sector.member_cnt}종목 중 ${sector.up_cnt}종목 상승">
    <span class="name">${unmapped ? "" : `<span class="dot" style="background:${seriesColor(sector.color)}"></span>`}${escapeHtml(sector.name)}</span>
    <span class="bar-cell">${bar(ret, max)}</span>
    <span class="num ${signClass(ret)}">${pct(ret)}</span>
    <span class="num hide-narrow">${bp(sector.contribution)}${share}</span>
    <span class="num hide-narrow ${delta.className}">${delta.text}</span>
    <span class="hide-narrow">${badges}</span>
  </div>`;
}

/**
 * @param {HTMLElement} container
 * @param {any} payload /sectors/returns 응답
 * @param {string} selectedGroup
 */
export function render(container, payload, selectedGroup) {
  const rows = payload.data.filter((s) => s.group_code !== "UNMAPPED" || (s.base_weight || 0) >= UNMAPPED_MIN_WEIGHT);
  if (!rows.length) {
    container.innerHTML = '<p class="empty">이 기간에 계산된 섹터가 없습니다.</p>';
    return;
  }
  const max = Math.max(...rows.map((s) => Math.abs(s["return"] || 0)), 1e-9);
  container.innerHTML = rows.map((s) => row(s, max, s.group_code === selectedGroup)).join("");
}

/** 차트 우상단의 시장 수익률 요약 (docs/06 §3.4) */
export function summaryHtml(summary, periodId) {
  const ret = summary.universe_return;
  const arrow = ret > 0 ? "▲" : ret < 0 ? "▼" : "–";
  return `${escapeHtml(periodId)} 시장수익률 <b class="${signClass(ret)}">${pct(ret)} ${arrow}</b>`
    + ` <span class="muted">· ${summary.member_cnt}종목 중 ${summary.up_cnt}종목 상승(${ratio(summary.up_ratio)})</span>`;
}
