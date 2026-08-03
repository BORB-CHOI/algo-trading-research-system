// 전략 저장소 — localStorage 'hts-strategies' 의 읽기/쓰기와 폼 드래프트 변환만 담당한다.
// 정량 값은 전부 사용자가 입력한 것만 저장한다 (ADR-0009 — 전략 숫자 하드코딩 금지).

import type { ConditionParamDef } from '../../api'

export const STORE_KEY = 'hts-strategies'

export type ScreenLogic = 'and' | 'or'
export type TargetKind = 'fib' | 'round' | 'manual'
export type PriceType = 'market' | 'limit'
export type QtyType = 'shares' | 'amount'

export type SavedCondition = { key: string; params: Record<string, number> }

export type Strategy = {
  screen: { logic: ScreenLogic; conditions: SavedCondition[] }
  entry: {
    key: string
    params: Record<string, number>
    target: TargetKind
    near: number
    manualPrice?: number
  }
  order: { priceType: PriceType; qtyType: QtyType; qty: number; validDays: number }
}

export type Strategies = Record<string, Strategy>

export function loadStrategies(): Strategies {
  try {
    const raw = JSON.parse(localStorage.getItem(STORE_KEY) ?? '{}') as Strategies
    if (!raw || typeof raw !== 'object') return {}
    const out: Strategies = {}
    for (const [name, s] of Object.entries(raw)) {
      if (s && s.screen && s.entry && s.order) out[name] = s
    }
    return out
  } catch {
    return {}
  }
}

export function persistStrategies(next: Strategies): Strategies {
  localStorage.setItem(STORE_KEY, JSON.stringify(next))
  return next
}

export function saveStrategy(all: Strategies, name: string, s: Strategy): Strategies {
  return persistStrategies({ ...all, [name]: s })
}

export function deleteStrategy(all: Strategies, name: string): Strategies {
  const next = { ...all }
  delete next[name]
  return persistStrategies(next)
}

// ── 폼 드래프트 (입력 중에는 전부 문자열 — 빈칸과 0 을 구분하기 위해) ──

export type StrategyDraft = {
  logic: ScreenLogic
  conditions: SavedCondition[]
  entryKey: string
  entryParams: Record<string, string>
  target: TargetKind
  near: string
  manualPrice: string
  priceType: PriceType
  qtyType: QtyType
  qty: string
  validDays: string
}

export function emptyDraft(): StrategyDraft {
  return {
    logic: 'and',
    conditions: [],
    entryKey: '',
    entryParams: {},
    target: 'fib',
    near: '',
    manualPrice: '',
    priceType: 'limit',
    qtyType: 'shares',
    qty: '',
    validDays: '',
  }
}

export function toDraft(s: Strategy): StrategyDraft {
  const entryParams: Record<string, string> = {}
  for (const [k, v] of Object.entries(s.entry.params ?? {})) entryParams[k] = String(v)
  return {
    logic: s.screen.logic,
    conditions: (s.screen.conditions ?? []).map((c) => ({ key: c.key, params: { ...c.params } })),
    entryKey: s.entry.key ?? '',
    entryParams,
    target: s.entry.target,
    near: s.entry.near == null ? '' : String(s.entry.near),
    manualPrice: s.entry.manualPrice == null ? '' : String(s.entry.manualPrice),
    priceType: s.order.priceType,
    qtyType: s.order.qtyType,
    qty: s.order.qty == null ? '' : String(s.order.qty),
    validDays: s.order.validDays == null ? '' : String(s.order.validDays),
  }
}

export type ParseResult<T> = { ok: true; value: T } | { ok: false; error: string }

function toNum(label: string, raw: string, int: boolean): ParseResult<number> {
  const t = raw.trim()
  if (!t) return { ok: false, error: `[${label}] 값을 입력하세요.` }
  const v = Number(t)
  if (!Number.isFinite(v)) return { ok: false, error: `[${label}] 숫자를 입력하세요.` }
  if (int && !Number.isInteger(v)) return { ok: false, error: `[${label}] 정수를 입력하세요.` }
  if (v <= 0) return { ok: false, error: `[${label}] 0보다 큰 값을 입력하세요.` }
  return { ok: true, value: v }
}

/** 카탈로그 스키마(조건·전략 공용)에 맞춰 입력값을 검증한다. 최소 1개 값은 있어야 한다. */
export function parseParams(
  defs: ConditionParamDef[],
  draft: Record<string, string>,
): ParseResult<Record<string, number>> {
  const params: Record<string, number> = {}
  for (const p of defs) {
    const raw = (draft[p.key] ?? '').trim()
    if (!raw) {
      if (p.required) return { ok: false, error: `[${p.label}] 값을 입력하세요.` }
      continue
    }
    const r = toNum(p.label, raw, p.type === 'int')
    if (!r.ok) return r
    params[p.key] = r.value
  }
  if (Object.keys(params).length === 0) return { ok: false, error: '값을 최소 1개 입력하세요.' }
  return { ok: true, value: params }
}

/** 드래프트 → 저장 형식. 미입력·형식 오류는 한국어 메시지로 돌려준다. */
export function toStrategy(d: StrategyDraft, entryDefs: ConditionParamDef[]): ParseResult<Strategy> {
  if (d.conditions.length === 0) return { ok: false, error: '① 종목 선정 조건을 1개 이상 추가하세요.' }
  if (!d.entryKey) return { ok: false, error: '② 매수 기준 전략을 선택하세요.' }

  const params = parseParams(entryDefs, d.entryParams)
  if (!params.ok) return params

  const near = toNum('근접 허용 오차', d.near, false)
  if (!near.ok) return near

  let manualPrice: number | undefined
  if (d.target === 'manual') {
    const p = toNum('목표가', d.manualPrice, false)
    if (!p.ok) return p
    manualPrice = p.value
  }

  const qty = toNum(d.qtyType === 'shares' ? '수량' : '금액', d.qty, d.qtyType === 'shares')
  if (!qty.ok) return qty

  const validDays = toNum('감시 유효기간', d.validDays, true)
  if (!validDays.ok) return validDays

  return {
    ok: true,
    value: {
      screen: { logic: d.logic, conditions: d.conditions },
      entry: {
        key: d.entryKey,
        params: params.value,
        target: d.target,
        near: near.value,
        ...(manualPrice == null ? {} : { manualPrice }),
      },
      order: {
        priceType: d.priceType,
        qtyType: d.qtyType,
        qty: qty.value,
        validDays: validDays.value,
      },
    },
  }
}
