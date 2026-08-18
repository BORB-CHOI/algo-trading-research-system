/** 장중 실시간 오늘 봉 — 차트를 연 종목만 (오너 결정 2026-08-18).
 *
 *  서버 `/api/live/bar` 가 나무 웹소켓으로 받은 오늘 누적 시가·고가·저가·현재가·거래량·거래대금을
 *  준다. 여기서는 그걸 **차트 마지막 봉에 어떻게 얹을지**만 정한다 — 파일엔 안 쓴다(확정 전 값).
 *
 *  - 일봉: 오늘 날짜로 봉 하나를 붙인다(이미 있으면 그 봉을 갱신).
 *  - 주봉·월봉: 서버가 준 마지막 봉(어제까지 합성된 진행 봉)에 오늘을 **합친다** — 시가는 그대로,
 *    고가·저가는 넓히고, 종가는 현재가, 거래량·거래대금은 더한다. 오늘이 새 주/달의 첫날이면
 *    오늘 날짜로 새 봉을 붙인다.
 *  - 파일에 오늘이 이미 들어갔으면(저녁 갱신 뒤) 아무것도 안 한다 — 같은 날이 두 번 나온다. */
import type { KLineData } from 'klinecharts'
import type { BarPeriod } from './ProChart'

export type LiveBar = {
  time: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  amount: number
}

export type LiveBarResponse = {
  code: string
  market: string
  market_open: boolean
  connected: boolean
  stored_last_day: string | null
  bar: LiveBar | null
  error: string | null
}

/** 오늘 0시(로컬) 타임스탬프 — 서버 봉 시각도 'YYYY-MM-DDT00:00:00' 로컬로 파싱한다. */
export function todayStart(now = new Date()): number {
  return new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
}

function isoDate(ts: number): string {
  const d = new Date(ts)
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${m}-${day}`
}

/** 같은 주(월~일)인가. 주봉 라벨은 그 주의 어느 거래일이든 될 수 있어서 주 단위로 본다. */
function sameWeek(a: number, b: number): boolean {
  const monday = (ts: number) => {
    const d = new Date(ts)
    const shift = (d.getDay() + 6) % 7 // 월=0
    return new Date(d.getFullYear(), d.getMonth(), d.getDate() - shift).getTime()
  }
  return monday(a) === monday(b)
}

function sameMonth(a: number, b: number): boolean {
  const x = new Date(a)
  const y = new Date(b)
  return x.getFullYear() === y.getFullYear() && x.getMonth() === y.getMonth()
}

/** 실시간 봉을 얹어야 하나. 파일에 오늘이 이미 있으면 false. */
export function shouldApply(res: LiveBarResponse, now = new Date()): boolean {
  if (!res.market_open || !res.bar) return false
  return !res.stored_last_day || res.stored_last_day < isoDate(todayStart(now))
}

/** 차트에 넣을 봉. `base` = 서버가 준 마지막 봉(오늘이 포함되지 않은 것). */
export function mergeLive(
  period: BarPeriod,
  base: KLineData | null,
  live: LiveBar,
  now = new Date(),
): KLineData {
  const today = todayStart(now)
  const fresh: KLineData = {
    timestamp: today,
    open: live.open,
    high: live.high,
    low: live.low,
    close: live.close,
    volume: live.volume,
    turnover: live.amount,
  }
  if (period === 'day' || !base) return fresh
  const same =
    period === 'week' ? sameWeek(base.timestamp, today) : period === 'month' ? sameMonth(base.timestamp, today) : false
  if (!same) return fresh
  return {
    timestamp: base.timestamp, // 같은 봉을 갱신하려면 타임스탬프가 같아야 한다(엔진 규칙)
    open: base.open,
    high: Math.max(base.high, live.high),
    low: Math.min(base.low, live.low),
    close: live.close,
    volume: (base.volume ?? 0) + live.volume,
    turnover: (base.turnover ?? 0) + live.amount,
  }
}
