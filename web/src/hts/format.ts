// 숫자 표기 공용 헬퍼 — HTS 표기 관례(억/조, 등락률 색상 클래스)를 한 곳에 모은다.

export const EOK = 1e8 // 1억 (원)

/** 원 단위 금액 → "1,234억" / "1.2조" */
export function fmtEok(won: number): string {
  const eok = won / EOK
  return eok >= 10000 ? `${(eok / 10000).toFixed(1)}조` : `${Math.round(eok).toLocaleString()}억`
}

/** 가격(원) → 천단위 콤마 */
export function fmtPrice(won: number): string {
  return Math.round(won).toLocaleString()
}

/** 등락률 → "+1.23%" / "-0.45%" / "-" */
export function fmtChg(chg: number | null | undefined): string {
  if (chg == null) return '-'
  return `${chg > 0 ? '+' : ''}${chg.toFixed(2)}%`
}

/** 등락률 → CSS 클래스 (한국식: 상승 빨강 up / 하락 파랑 down) */
export function chgClass(chg: number | null | undefined): string {
  if (chg == null || chg === 0) return 'flat'
  return chg > 0 ? 'up' : 'down'
}
