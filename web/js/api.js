// @ts-check
/** API 호출 (docs/05). 오류 봉투를 ApiError로 바꾼다. */

const BASE = "/api/v1";

export class ApiError extends Error {
  /** @param {number} status @param {string} code @param {string} message @param {string=} field */
  constructor(status, code, message, field) {
    super(message);
    this.status = status;
    this.code = code;
    this.field = field;
  }
}

/**
 * @param {string} path
 * @param {Record<string, string|number|null|undefined>} [params]
 * @returns {Promise<any>}
 */
export async function get(path, params) {
  const url = new URL(BASE + path, location.origin);
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, String(value));
  }
  let response;
  try {
    response = await fetch(url);
  } catch (error) {
    throw new ApiError(0, "NETWORK", "서버에 연결하지 못했다");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const error = body.error || {};
    throw new ApiError(response.status, error.code || "ERROR", error.message || response.statusText, error.field);
  }
  return response.json();
}
