// 전략 저장소 — localStorage 'hts-strategies' 의 읽기/쓰기와 폼 드래프트 변환만 담당한다.
// 정량 값은 전부 사용자가 입력한 것만 저장한다 (ADR-0009 — 전략 숫자 하드코딩 금지).

import type { ConditionParamDef } from '../../api'

export const STORE_KEY = 'hts-strategies'

export type ScreenLogic = 'and' | 'or'
export type PriceType = 'market' | 'limit'
export type QtyType = 'shares' | 'amount'
export type CreditType = 'cash' | 'credit'

export type SavedCondition = { key: string; params: Record<string, number> }

/** 분할 매수 한 차수. 되돌림 비율에서 목표가가 나오고, 사용자가 값을 덮어쓸 수 있다.
 *  오너 요구: "각각 다 커스텀으로 추가·해제 하면서" — 그래서 차수가 고정 3개가 아니다. */
export type BuyStage = {
  id: string
  /** 피보나치 되돌림 비율 (0.382 = 38.2% 되돌림 지점) */
  ratio: number
  /** 이 차수에 넣을 비중. 전체 합 대비 비율로 쓴다 — 절대 수량이 아니다. */
  weight: number
  enabled: boolean
  /** 자동 계산(레벨→라운드 피겨) 대신 직접 지정한 가격. 비면 자동. */
  priceOverride?: number
}

/** 분할 매도 한 차수. 기준점 대비 반등률에서 목표가가 나온다. */
export type SellStage = {
  id: string
  reboundPct: number
  weight: number
  enabled: boolean
  priceOverride?: number
}

/** 매도 반등률의 기준점. 오너가 "반등 몇 %"라 했을 때 무엇 대비인지가 갈려서 고르게 둔다. */
export type SellBasis = 'avg_entry' | 'lowest_fill' | 'anchor_high'

export type Strategy = {
  /** name = ① 에서 만든 조건검색식 이름. 없으면 전체 종목이 대상. */
  screen: { name?: string; logic: ScreenLogic; conditions: SavedCondition[] }
  /** 진입 기법과 그 파라미터. 목표가·분할 레벨은 기법이 계산한다(입력값 ❌). */
  entry: { key: string; params: Record<string, number> }
  /** 분할 매수·매도 설계. 가격은 여기 없다 — 기법 파라미터와 종목에서 계산된다. */
  split: {
    buy: BuyStage[]
    sell: SellStage[]
    sellBasis: SellBasis
    /** 레벨에서 ±몇 % 안의 라운드 피겨를 목표가로 삼을지 (전략 파라미터) */
    roundTolerancePct: number
  }
  order: {
    priceType: PriceType
    qtyType: QtyType
    qty: number
    credit: CreditType
  }
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
  screenName: string
  logic: ScreenLogic
  conditions: SavedCondition[]
  entryKey: string
  entryParams: Record<string, string>
  buy: BuyStage[]
  sell: SellStage[]
  sellBasis: SellBasis
  roundTolerancePct: string
  priceType: PriceType
  qtyType: QtyType
  qty: string
  credit: CreditType
}

let stageSeq = 0
export function newStageId(): string {
  return `s${Date.now().toString(36)}${(stageSeq++).toString(36)}`
}

/** 새 분할 차수. 값은 비워 두지 않고 눈에 보이는 자리표시로 넣되, 저장 시 검증한다.
 *  ADR-0009 상 "기본 전략값"을 서버가 아는 게 금지지, 화면이 빈 폼을 주는 건 아니다 —
 *  다만 사용자가 안 본 값이 저장되면 안 되므로 추가 시점의 값은 항상 화면에 보인다. */
export function newBuyStage(ratio: number, weight: number): BuyStage {
  return { id: newStageId(), ratio, weight, enabled: true }
}

export function newSellStage(reboundPct: number, weight: number): SellStage {
  return { id: newStageId(), reboundPct, weight, enabled: true }
}

export function emptyDraft(): StrategyDraft {
  return {
    screenName: '',
    logic: 'and',
    conditions: [],
    entryKey: '',
    entryParams: {},
    buy: [],
    sell: [],
    sellBasis: 'avg_entry',
    roundTolerancePct: '',
    priceType: 'limit',
    qtyType: 'shares',
    qty: '',
    credit: 'cash',
  }
}

export function toDraft(s: Strategy): StrategyDraft {
  const entryParams: Record<string, string> = {}
  for (const [k, v] of Object.entries(s.entry.params ?? {})) entryParams[k] = String(v)
  return {
    screenName: s.screen.name ?? '',
    logic: s.screen.logic,
    conditions: (s.screen.conditions ?? []).map((c) => ({ key: c.key, params: { ...c.params } })),
    entryKey: s.entry.key ?? '',
    entryParams,
    // split 이 없는 옛 저장본도 열려야 한다 — 빈 배열로 받는다.
    buy: (s.split?.buy ?? []).map((b) => ({ ...b })),
    sell: (s.split?.sell ?? []).map((x) => ({ ...x })),
    sellBasis: s.split?.sellBasis ?? 'avg_entry',
    roundTolerancePct: s.split?.roundTolerancePct == null ? '' : String(s.split.roundTolerancePct),
    priceType: s.order.priceType,
    qtyType: s.order.qtyType,
    qty: s.order.qty == null ? '' : String(s.order.qty),
    credit: s.order.credit ?? 'cash',
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

/** 켜져 있는 분할 차수만 검증한다 — 꺼둔 차수는 값이 비어 있어도 저장을 막지 않는다.
 *  (오너가 차수를 켜고 끄며 실험하는 게 전제라, 끈 차수까지 채우라고 하면 방해가 된다.) */
function checkStages(d: StrategyDraft): ParseResult<true> {
  const buy = d.buy.filter((b) => b.enabled)
  if (buy.length === 0) return { ok: false, error: '분할 매수 차수를 1개 이상 켜세요.' }
  for (const [i, b] of buy.entries()) {
    if (b.ratio <= 0 || b.ratio >= 1) {
      return { ok: false, error: `매수 ${i + 1}차 되돌림 비율은 0~1 사이여야 합니다.` }
    }
    if (b.weight <= 0) return { ok: false, error: `매수 ${i + 1}차 비중을 입력하세요.` }
  }
  for (const [i, x] of d.sell.filter((s) => s.enabled).entries()) {
    if (x.reboundPct <= 0) return { ok: false, error: `매도 ${i + 1}차 반등률을 입력하세요.` }
    if (x.weight <= 0) return { ok: false, error: `매도 ${i + 1}차 비중을 입력하세요.` }
  }
  return { ok: true, value: true }
}

/** 드래프트 → 저장 형식. 미입력·형식 오류는 한국어 메시지로 돌려준다. */
export function toStrategy(d: StrategyDraft, entryDefs: ConditionParamDef[]): ParseResult<Strategy> {
  // 조건이 없으면 전체 종목이 대상이다 — 막지 않고 그대로 저장한다(화면에서 명시).
  if (!d.entryKey) return { ok: false, error: '진입 기법을 선택하세요.' }

  const params = parseParams(entryDefs, d.entryParams)
  if (!params.ok) return params

  const qty = toNum(d.qtyType === 'shares' ? '수량' : '금액', d.qty, d.qtyType === 'shares')
  if (!qty.ok) return qty

  const stages = checkStages(d)
  if (!stages.ok) return stages

  const tol = toNum('라운드 피겨 허용폭', d.roundTolerancePct, false)
  if (!tol.ok) return tol

  return {
    ok: true,
    value: {
      screen: {
        ...(d.screenName ? { name: d.screenName } : {}),
        logic: d.logic,
        conditions: d.conditions,
      },
      entry: { key: d.entryKey, params: params.value },
      split: {
        buy: d.buy.map((b) => ({ ...b })),
        sell: d.sell.map((s) => ({ ...s })),
        sellBasis: d.sellBasis,
        roundTolerancePct: tol.value,
      },
      order: {
        priceType: d.priceType,
        qtyType: d.qtyType,
        qty: qty.value,
        credit: d.credit,
      },
    },
  }
}
