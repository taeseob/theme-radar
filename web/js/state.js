// @ts-check
/** 화면 상태는 URL 쿼리스트링이 단일 출처다 (docs/06 §2). */

export const DEFAULTS = {
  universe: "KR_COMMON",
  scheme: "",          // 비어 있으면 시장의 첫 스킴
  period: "W",
  range: "26",
  top: "0",
  mode: "rank",        // rank | return
  basis: "period",     // period | ma — 순위와 상승률의 기준 (docs/06 §3.5)
  above: "",           // "1"이면 이동평균 위에 있는 섹터만 그린다 (basis=ma)
  focus: "",           // 더블클릭으로 강조한 섹터. 쉼표로 잇는다. 비어 있으면 전부 또렷하다 (docs/06 §3.2)
  view: "chart",       // chart | table — 표 대체 표현 (docs/06 §8)
  group: "",           // 드릴다운으로 연 섹터
  pid: "",             // 선택한 기간 (비어 있으면 최신)
  cap: "line",         // line | candle — 섹터 시가총액 차트 모양 (docs/06 §5.4)
  ma: "",              // "1"이면 시가총액 차트에 이동평균선을 그린다
  ma_n: "5",           // 이동평균 기간 수. 순위 기준·이격도·시가총액 차트가 함께 쓴다
  side: "",            // 오른쪽 패널에서 함께 보는 섹터. 비어 있으면 시장 지수만 (docs/06 §3.6)
  side_ma: "",         // "1"이면 오른쪽 패널 차트에 이동평균선을 그린다
  side_ma_n: "5",      // 오른쪽 패널의 이동평균 기간 수
  event_type: "",
};

/** @returns {Record<string, string>} */
export function read() {
  const params = new URLSearchParams(location.search);
  const state = { ...DEFAULTS };
  for (const key of Object.keys(DEFAULTS)) {
    const value = params.get(key);
    if (value !== null) state[key] = value;
  }
  return state;
}

/**
 * 상태를 바꾸고 URL에 반영한다. 같은 값이면 아무것도 하지 않는다.
 * `silent`는 URL만 고쳐 쓰고 다시 그리지 않는다 (기본값 보정처럼 화면이 이미 맞는 경우).
 */
export function update(changes, { replace = false, silent = false } = {}) {
  const state = { ...read(), ...changes };
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(state)) {
    if (value && value !== DEFAULTS[key]) params.set(key, String(value));
  }
  const url = `${location.pathname}${params.toString() ? `?${params}` : ""}`;
  if (url === location.pathname + location.search) return false;
  if (replace || silent) history.replaceState(null, "", url);
  else history.pushState(null, "", url);
  if (!silent) window.dispatchEvent(new CustomEvent("statechange", { detail: state }));
  return true;
}

export function onChange(handler) {
  window.addEventListener("statechange", () => handler(read()));
  window.addEventListener("popstate", () => handler(read()));
}
