// @ts-check
/** 기간 스냅샷 패널 (docs/06 §4). 막대는 0을 중심으로 양방향이다. */
import { bp, disparity, escapeHtml, pct, pp, rankDelta, ratio, signClass } from "./format.js";
import { seriesColor } from "./theme.js";

const UNMAPPED_MIN_WEIGHT = 0.001;   // 시총 비중 0.1% 미만인 미매핑 행은 감춘다

function bar(value, max) {
  if (!value || !max) return '<span class="zero"></span>';
  const width = Math.min(50, Math.abs(value) / max * 50);
  const side = value > 0 ? `left:50%;width:${width}%` : `left:${50 - width}%;width:${width}%`;
  return `<span class="zero"></span><span class="fill ${signClass(value)}-bg" style="${side}"></span>`;
}

/** 막대 눈금. 기준 0이 어디인지와 양 끝이 얼마인지를 머리글에 적는다 */
function axis(max) {
  return `<span class="bar-cell axis"><span class="zero"></span>
    <span class="axis-end left">${pct(-max)}</span><span class="axis-zero">0</span>
    <span class="axis-end right">${pct(max)}</span></span>`;
}

/**
 * 값 행과 같은 격자의 머리글. 정렬 기준인 열은 눌러서 바꿀 수 있고 ▼로 표시한다.
 * 정렬 키는 /sectors/returns의 sort 값과 같고(docs/05 §3.2), 이격도만 화면이 맡는다.
 */
function head(max, sort, maLength, index) {
  const column = (label, key, narrow = "", title = "") => {
    const active = key === sort;
    const attrs = key ? ` data-sort="${key}" role="button" tabindex="0" aria-sort="${active ? "descending" : "none"}"` : "";
    return `<span class="num${narrow}${key ? " sortable" : ""}${active ? " active" : ""}"${attrs}`
      + `${title ? ` title="${escapeHtml(title)}"` : ""}>${label}${active ? " ▼" : ""}</span>`;
  };
  return `<div class="row head">
    <span class="name">섹터</span>
    ${axis(max)}
    ${column("수익률", "return")}
    ${column("지수 대비", "", " hide-narrow hide-mid",
             `섹터 수익률 − 같은 기간 ${index?.name || "시장 지수"} 수익률 (%p).`
             + ` 이 기간 지수 수익률은 ${pct(index?.ret)}다`)}
    ${column("시총 비중", "weight", " hide-narrow")}
    ${column("시장 기여", "contribution", " hide-narrow")}
    <span class="num hide-narrow" title="시장 수익률 대비 이 섹터 기여도의 비율">시장 대비</span>
    ${column("이격도", "disparity", " hide-narrow",
             `기간 말 섹터 시총 ÷ ${maLength ? `${maLength}기간 ` : ""}이동평균 × 100. 100보다 크면 이동평균 위다`)}
    ${column("상승률", "", " hide-narrow hide-mid",
             `${maLength ? `${maLength}기간 ` : ""}이동평균의 직전 기간 대비 상승률. 이격도가 이동평균과 얼마나 떨어졌는지라면`
             + " 이 값은 이동평균 자체가 어느 방향으로 움직이는지다")}
    <span class="num hide-narrow">순위 변동</span>
    <span class="hide-narrow">특징</span>
  </div>`;
}

function row(sector, max, selected, point, indexReturn) {
  const unmapped = sector.group_code === "UNMAPPED";
  const ret = sector["return"];            // 응답의 필드 이름은 별칭 "return"이다 (docs/05 §3.2)
  const delta = rankDelta(sector.rank_delta);
  const badges = sector.badges.map((b) => `<span class="badge">${escapeHtml(b)}</span>`).join("");
  // 시장 수익률 대비 비율은 보조 값이다. 시장과 방향이 반대면 음수 비율이 되어 읽히지 않으므로 뺀다
  const share = (sector.contribution_share || 0) >= 0.005 ? ratio(sector.contribution_share) : "";
  // 지수 수익률은 이 기간 모든 섹터에 같은 값이라, 이 열로 정렬해도 수익률 정렬과 순서가 같다
  const excess = ret === null || ret === undefined || indexReturn === null ? null : ret - indexReturn;
  return `<div class="row ${unmapped ? "unmapped" : "clickable"}${selected ? " selected" : ""}"
       ${unmapped ? "" : `data-group="${escapeHtml(sector.group_code)}" role="button" tabindex="0"`}
       title="${escapeHtml(sector.name)} · ${sector.member_cnt}종목 중 ${sector.up_cnt}종목 상승">
    <span class="name">${unmapped ? "" : `<span class="dot" style="background:${seriesColor(sector.color)}"></span>`}${escapeHtml(sector.name)}</span>
    <span class="bar-cell">${bar(ret, max)}</span>
    <span class="num ${signClass(ret)}">${pct(ret)}</span>
    <span class="num hide-narrow hide-mid ${excess === null ? "muted" : signClass(excess)}">${pp(excess)}</span>
    <span class="num hide-narrow">${ratio(sector.base_weight, 2)}</span>
    <span class="num hide-narrow">${bp(sector.contribution)}</span>
    <span class="num muted hide-narrow">${share}</span>
    <span class="num hide-narrow ${point ? signClass(point.disparity - 1) : "muted"}">${disparity(point?.disparity)}</span>
    <span class="num hide-narrow hide-mid ${point && point.ret !== null ? signClass(point.ret) : "muted"}">${pct(point?.ret)}</span>
    <span class="num hide-narrow ${delta.className}">${delta.text}</span>
    <span class="hide-narrow">${badges}</span>
  </div>`;
}

/**
 * 이격도 정렬은 화면이 한다. 이동평균을 서버에 두지 않으므로(docs/03 §14.3) /sectors/returns의 sort에는 없다.
 * 값이 없는 섹터는 뒤로 보내고, 미매핑 행은 늘 마지막이다.
 */
function ordered(rows, sort, points) {
  if (sort !== "disparity") return rows;
  const value = (row) => points?.get(row.group_code)?.disparity ?? -Infinity;
  return [...rows].sort((a, b) => {
    const unmapped = Number(a.group_code === "UNMAPPED") - Number(b.group_code === "UNMAPPED");
    return unmapped || value(b) - value(a);
  });
}

/**
 * @param {HTMLElement} container
 * @param {any} payload /sectors/returns 응답
 * @param {string} selectedGroup
 * @param {{length: number, points: Map<string, any>}|null} ma 섹터별 이동평균·이격도 (ma.js)
 * @param {string} sort 정렬 기준. 이격도는 화면이 맡는다
 * @param {{name: string, ret: number|null}} index 같은 기간 시장 지수 수익률 (docs/03 §15). 없으면 ret이 null이다
 */
export function render(container, payload, selectedGroup, ma, sort, index) {
  const rows = payload.data.filter((s) => s.group_code !== "UNMAPPED" || (s.base_weight || 0) >= UNMAPPED_MIN_WEIGHT);
  if (!rows.length) {
    container.innerHTML = '<p class="empty">이 기간에 계산된 섹터가 없습니다.</p>';
    return;
  }
  const max = Math.max(...rows.map((s) => Math.abs(s["return"] || 0)), 1e-9);
  container.innerHTML = head(max, sort, ma?.length, index)
    + ordered(rows, sort, ma?.points)
      .map((s) => row(s, max, s.group_code === selectedGroup, ma?.points.get(s.group_code), index?.ret ?? null))
      .join("");
}

/** 차트 우상단의 시장 수익률 요약 (docs/06 §3.4) */
export function summaryHtml(summary, periodId) {
  const ret = summary.universe_return;
  const arrow = ret > 0 ? "▲" : ret < 0 ? "▼" : "–";
  return `${escapeHtml(periodId)} 시장수익률 <b class="${signClass(ret)}">${pct(ret)} ${arrow}</b>`
    + ` <span class="muted">· ${summary.member_cnt}종목 중 ${summary.up_cnt}종목 상승(${ratio(summary.up_ratio)})</span>`;
}
