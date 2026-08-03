// 시세포착 감시 목록 — 목표가에 도달하면 주문을 낼 "감시"의 정의만 저장한다.
// 주문은 나가지 않는다. 실제 주문은 KIS 연동 후 결정론적 코드가 낸다(CLAUDE.md §MCP).

import type { PriceType, QtyType } from './strategyStore'

export const WATCH_KEY = 'hts-watchorders'

export type WatchSide = 'buy' | 'sell'
export type WatchState = 'run' | 'hold'

export type WatchOrder = {
  id: string
  side: WatchSide
  code: string
  name: string
  target: number
  qty: number
  qtyType: QtyType
  priceType: PriceType
  tick: number
  credit: 'cash' | 'credit'
  from: string
  to: string
  state: WatchState
  strategy?: string
}

export function loadWatches(): WatchOrder[] {
  try {
    const raw = JSON.parse(localStorage.getItem(WATCH_KEY) ?? '[]') as WatchOrder[]
    return Array.isArray(raw) ? raw.filter((w) => w && w.id && w.code) : []
  } catch {
    return []
  }
}

export function saveWatches(next: WatchOrder[]): WatchOrder[] {
  localStorage.setItem(WATCH_KEY, JSON.stringify(next))
  return next
}

export function newId(): string {
  return crypto.randomUUID?.() ?? `w${Date.now()}${Math.floor(Math.random() * 1000)}`
}

/** from 부터 days 일 뒤까지 — KIS 는 최대 30일. */
export function periodFrom(days: number): { from: string; to: string } {
  const d0 = new Date()
  const d1 = new Date(d0.getTime() + Math.min(days, 30) * 86_400_000)
  const iso = (d: Date) => d.toISOString().slice(0, 10)
  return { from: iso(d0), to: iso(d1) }
}

export function fmtPeriod(w: { from: string; to: string }): string {
  return `${w.from.replace(/-/g, '.')} ~ ${w.to.replace(/-/g, '.')}`
}
