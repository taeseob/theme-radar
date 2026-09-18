// @ts-check
/** 특이사항 목록 (docs/06 §1, docs/07 §11.2). 섹터 시총 대비 규모가 큰 순으로 본다. */
import { cap, escapeHtml, ratio, signClass } from "./format.js";

const LABEL = {
  LISTING: "편입·상장", DELISTING: "편출·폐지", RECLASS: "분류 변경",
  SHARE_CHANGE: "주식수 급변", CORP_ACTION: "기업행위", DATA_GAP: "데이터 공백",
};

/** @param {string} type */
export function eventLabel(type) {
  return LABEL[type] || type;
}

/** 출처 코드의 표시 이름 (docs/07 §11.2). 모르는 코드는 코드 그대로 보여 준다 */
const SOURCE = {
  KIND_LISTING: "KIND 상장목록", FDR_KRX_DELISTING: "FDR 폐지목록", WIKI_SP500: "위키백과 S&P 500",
  DAUM_QUOTE: "다음 시세", FDR_KRX_CACHE: "FDR KRX", SEC_DEI: "SEC 공시", YF_INFO: "yfinance",
  NAVER_FACTOR_JUMP: "네이버 수정계수", YFINANCE_SPLIT: "yfinance 분할", MANUAL: "수기",
  INTERNAL: "자체 판정",
};

/** 한 사건에 출처가 둘 이상이면 쉼표로 이어 온다 */
function sourceLabel(source) {
  if (!source) return "—";
  return source.split(",").map((code) => SOURCE[code] || code).join(" · ");
}

function row(event, currency) {
  const span = event.end_date && event.end_date !== event.event_date ? ` ~ ${event.end_date}` : "";
  const subject = event.ticker
    ? `${escapeHtml(event.name || "")} <span class="muted">${escapeHtml(event.ticker)}</span>`
    : '<span class="muted">—</span>';
  return `<div class="row">
    <span class="num date">${escapeHtml(event.event_date)}${escapeHtml(span)}</span>
    <span><span class="badge">${escapeHtml(eventLabel(event.event_type))}</span></span>
    <span class="name">${subject}</span>
    <span class="name muted hide-narrow">${escapeHtml(event.group_name || event.group_code || "")}</span>
    <span class="num hide-narrow ${signClass(event.sector_share)}">${event.sector_share === null || event.sector_share === undefined
      ? "—" : ratio(event.sector_share, 2)}</span>
    <span class="num muted hide-narrow">${cap(event.market_cap, currency)}</span>
    <span class="name muted hide-narrow" title="${escapeHtml(event.source || "")}">${escapeHtml(sourceLabel(event.source))}</span>
    <span class="detail muted">${escapeHtml(event.detail)}</span>
  </div>`;
}

/**
 * @param {HTMLElement} container
 * @param {any} payload /events 응답
 * @param {string} currency
 */
export function render(container, payload, currency) {
  if (!payload.data.length) {
    container.innerHTML = '<p class="empty">해당 조건의 특이사항이 없습니다.</p>';
    return;
  }
  const head = `<div class="row head"><span class="num date">날짜</span><span>유형</span><span class="name">종목</span>
    <span class="name hide-narrow">섹터</span><span class="num hide-narrow">섹터 대비</span>
    <span class="num hide-narrow">시총</span><span class="name hide-narrow">출처</span>
    <span class="detail">내용</span></div>`;
  container.innerHTML = head + payload.data.map((event) => row(event, currency)).join("");
}

/** 유형 선택 목록에 건수를 붙인다 */
export function decorateTypes(select, counts) {
  for (const option of select.options) {
    const base = option.dataset.label || (option.dataset.label = option.textContent || "");
    const count = option.value ? counts[option.value] : Object.values(counts).reduce((a, b) => a + b, 0);
    option.textContent = count ? `${base} (${count})` : base;
    option.disabled = Boolean(option.value) && !count;
  }
}
