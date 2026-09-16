// @ts-check
/** 표기 규칙 (docs/06 §7). 수익률 소수 2자리 %, 기여도 bp, 시총은 시장 단위. */

/** @param {number|null|undefined} value @param {number} [digits] */
export function pct(value, digits = 2) {
  if (value === null || value === undefined) return "—";
  return `${value >= 0 ? "+" : ""}${(value * 100).toFixed(digits)}%`;
}

/** @param {number|null|undefined} value */
export function bp(value) {
  if (value === null || value === undefined) return "—";
  const basis = value * 10000;
  const digits = Math.abs(basis) < 100 ? 1 : 0;
  return `${basis >= 0 ? "+" : ""}${basis.toFixed(digits)}bp`;
}

/** @param {number|null|undefined} value @param {number} [digits] */
export function ratio(value, digits = 0) {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

/** @param {number|null|undefined} value @param {string} currency */
export function cap(value, currency) {
  if (value === null || value === undefined) return "—";
  if (currency === "KRW") {
    if (Math.abs(value) >= 1e12) return `${(value / 1e12).toFixed(1)}조원`;
    return `${Math.round(value / 1e8).toLocaleString()}억원`;
  }
  if (Math.abs(value) >= 1e9) return `$${(value / 1e9).toFixed(1)}B`;
  return `$${(value / 1e6).toFixed(0)}M`;
}

/** 순위 변동 화살표. 색만으로 구분하지 않도록 기호를 함께 쓴다 (docs/06 §8) */
export function rankDelta(delta) {
  if (delta === null || delta === undefined) return { text: "–", className: "muted" };
  if (delta > 0) return { text: `▲${delta}`, className: "delta up" };
  if (delta < 0) return { text: `▼${-delta}`, className: "delta down" };
  return { text: "–", className: "muted" };
}

/** 축약 기간 라벨. 연도가 바뀌는 지점에만 연도를 붙인다 */
export function periodLabels(ids) {
  let lastYear = "";
  return ids.map((id) => {
    const [year, rest] = id.split("-");
    const label = rest.startsWith("W") ? rest : `${rest}월`;
    const withYear = year !== lastYear ? `${label}\n${year}` : label;
    lastYear = year;
    return withYear;
  });
}

export function signClass(value) {
  if (value === null || value === undefined || value === 0) return "";
  return value > 0 ? "pos" : "neg";
}

/** @param {string} html */
export function el(html) {
  const template = document.createElement("template");
  template.innerHTML = html.trim();
  return /** @type {HTMLElement} */ (template.content.firstElementChild);
}

export function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
