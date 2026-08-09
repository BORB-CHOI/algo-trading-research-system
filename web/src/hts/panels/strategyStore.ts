// 전략 저장소 — localStorage 'hts-strategies' 의 읽기/쓰기와 폼 드래프트 변환만 담당한다.
import { writeBoth } from '../store'
// 정량 값은 전부 사용자가 입력한 것만 저장한다 (ADR-0009 — 전략 숫자 하드코딩 금지).

import type { ConditionParamDef } from '../../api'
import { DEFAULT_FIB_STOP_RATIO, FIB_STOP_CHOICES, isFixedDefinition } from './strategyOne'

export const STORE_KEY = 'hts-strategies'

export type ScreenLogic = 'and' | 'or'
export type PriceType = 'market' | 'limit'
export type QtyType = 'shares' | 'amount'
export type CreditType = 'cash' | 'credit'

export type SavedCondition = { key: string; params: Record<string, number | string> }

/** within(이내) 파라미터 추가(2026-08-05) 이전 저장분 이관 — '이내' 없음 = 당일(1).
 *  기본값 주입이 아니라 기존 검색식의 원래 의미(당일 발생만) 보존이다. */
const NEEDS_WITHIN = new Set(['new_high', 'new_low', 'gap_up'])
export function migrateConditions(conds: SavedCondition[]): SavedCondition[] {
  return conds.map((c) =>
    NEEDS_WITHIN.has(c.key) && c.params['within'] == null
      ? { ...c, params: { ...c.params, within: 1 } }
      : c,
  )
}

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

/** 손절 — 평단 대비 % 또는 기준선(사이클 저점·직접 가격) ±N호가.
 *  'anchor_start'(급등 폐기, ADR-0013 개정)·'avwap'(VWAP 폐기, ADR-0014)은 삭제 —
 *  옛 저장분은 toDraft 에서 사이클 저점으로 이관한다. */
export type StopRule = {
  enabled: boolean
  mode: 'pct' | 'support' | 'fib'
  pct?: number // mode=pct: 평단에서 몇 % 아래
  source: 'cycle_low' | 'custom' // mode=support 기준선
  customPrice?: number
  tickOffset: number // 기준선에서 ±N호가 (음수 = 아래)
  /** mode=fib: 어느 되돌림 선에 걸까. 기본 0.786 = 5번째 선 (오너 2026-08-10). */
  fibRatio?: number
}

export type Strategy = {
  /** name = ① 에서 만든 조건검색식 이름. 없으면 전체 종목이 대상. */
  screen: { name?: string; logic: ScreenLogic; conditions: SavedCondition[] }
  /** 진입 기법(케이스 검사기 오버레이용). 전략 1호 시뮬레이션은 안 쓴다 — 선택 사항. */
  entry?: { key: string; params: Record<string, number | string> }
  /** 분할 매수·매도 설계. 가격은 여기 없다 — 기법 파라미터와 종목에서 계산된다.
   *  목표가 = 각 레벨에서 가장 가까운 지지/저항선 ± 호가 오프셋(ADR-0014).
   *  roundTolerancePct 는 라운드 피겨 방식 폐기로 옛 저장본 호환용으로만 남는다. */
  split: {
    buy: BuyStage[]
    sell: SellStage[]
    sellBasis: SellBasis
    buyTickOffset?: number // 지지/저항선에서 ±N호가 (음수 = 아래)
    sellTickOffset?: number
    /** 매수 차수 사이 최소 간격(%). 없으면(옛 저장본) 0 = 안 씀.
     *  오너 2026-08-09: "-10%나 -15%의 차이는 넘게 봐야한다". */
    buyMinGapPct?: number
    roundTolerancePct?: number
  }
  /** 손절. 없으면(옛 저장본) 손절 미사용으로 연다. */
  stop?: StopRule
  /** 다 팔고 나서 같은 자리에 또 오면 다시 살지. 없으면 안 산다(옛 저장본 = 그때 동작).
   *  전 기간 백테스트에서만 뜻이 있다 — ③ 시뮬레이션은 라운드 하나만 그린다. */
  reenterSameWave?: boolean
  /** 주문조건 — 주문조건 카드 삭제(2026-08-05)로 새 저장본에는 없다. 옛 저장본 호환용. */
  order?: {
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
      // entry·order 는 이제 선택 사항 — 있어야 열리는 조건으로 걸면 새 저장본이 전부 버려진다.
      if (s && s.screen && s.split) {
        out[name] = { ...s, screen: { ...s.screen, conditions: migrateConditions(s.screen.conditions ?? []) } }
      }
    }
    return out
  } catch {
    return {}
  }
}

export function persistStrategies(next: Strategies): Strategies {
  writeBoth(STORE_KEY, next)
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
  buyTickOffset: string // 지지/저항선에서 ±N호가 (음수 = 아래)
  sellTickOffset: string
  buyMinGapPct: string // 매수 차수 사이 최소 간격(%). '0' = 안 씀
  stopEnabled: boolean
  stopMode: 'pct' | 'support' | 'fib'
  stopPct: string
  stopSource: 'cycle_low' | 'custom'
  stopCustom: string
  stopTicks: string // ±N호가 (음수 = 아래)
  stopFibRatio: string // mode=fib: 되돌림 비율 ('0.786' = 5번째 선)
  reenterSameWave: boolean // 다 팔고 같은 자리에 또 오면 다시 살지 (전 기간 백테스트)
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
    buyTickOffset: '0',
    sellTickOffset: '0',
    buyMinGapPct: '10',
    stopEnabled: false,
    stopMode: 'pct',
    stopPct: '',
    stopSource: 'cycle_low',
    stopCustom: '',
    stopTicks: '0',
    stopFibRatio: String(DEFAULT_FIB_STOP_RATIO),
    reenterSameWave: false,
    priceType: 'limit',
    qtyType: 'shares',
    qty: '',
    credit: 'cash',
  }
}

export function toDraft(s: Strategy): StrategyDraft {
  const entryParams: Record<string, string> = {}
  for (const [k, v] of Object.entries(s.entry?.params ?? {})) entryParams[k] = String(v)
  return {
    screenName: s.screen.name ?? '',
    logic: s.screen.logic,
    conditions: (s.screen.conditions ?? []).map((c) => ({ key: c.key, params: { ...c.params } })),
    entryKey: s.entry?.key ?? '',
    entryParams,
    // split 이 없는 옛 저장본도 열려야 한다 — 빈 배열로 받는다.
    buy: (s.split?.buy ?? []).map((b) => ({ ...b })),
    sell: (s.split?.sell ?? []).map((x) => ({ ...x })),
    sellBasis: s.split?.sellBasis ?? 'avg_entry',
    buyTickOffset: String(s.split?.buyTickOffset ?? 0),
    sellTickOffset: String(s.split?.sellTickOffset ?? 0),
    // 옛 저장본엔 없다 — 그때 계산된 값이 바뀌면 안 되니 0(안 씀)으로 연다.
    buyMinGapPct: String(s.split?.buyMinGapPct ?? 0),
    stopEnabled: s.stop?.enabled ?? false,
    stopMode: s.stop?.mode ?? 'pct',
    stopPct: s.stop?.pct == null ? '' : String(s.stop.pct),
    // 옛 저장분의 'anchor_start'(급등 폐기)·'avwap'(VWAP 폐기)은 사이클 저점으로 이관.
    stopSource: s.stop?.source === 'custom' ? 'custom' : 'cycle_low',
    stopCustom: s.stop?.customPrice == null ? '' : String(s.stop.customPrice),
    stopTicks: String(s.stop?.tickOffset ?? 0),
    stopFibRatio: String(s.stop?.fibRatio ?? DEFAULT_FIB_STOP_RATIO),
    reenterSameWave: s.reenterSameWave ?? false,
    priceType: s.order?.priceType ?? 'limit',
    qtyType: s.order?.qtyType ?? 'shares',
    qty: s.order?.qty == null ? '' : String(s.order.qty),
    credit: s.order?.credit ?? 'cash',
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
): ParseResult<Record<string, number | string>> {
  const params: Record<string, number | string> = {}
  for (const p of defs) {
    const raw = (draft[p.key] ?? '').trim()
    if (!raw) {
      if (p.required) return { ok: false, error: `[${p.label}] 값을 입력하세요.` }
      continue
    }
    if (p.type === 'select') {
      if (p.choices.length && !p.choices.includes(raw)) {
        return { ok: false, error: `[${p.label}] 은 ${p.choices.join(' / ')} 중 하나여야 합니다.` }
      }
      params[p.key] = raw
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
 *  (오너가 차수를 켜고 끄며 실험하는 게 전제라, 끈 차수까지 채우라고 하면 방해가 된다.)
 *  비중은 **절대 %** — 합이 100 을 넘으면 과매수라 저장을 막는다 (오너 확정 2026-08-05). */
function checkStages(d: StrategyDraft): ParseResult<true> {
  const buy = d.buy.filter((b) => b.enabled)
  if (buy.length === 0) return { ok: false, error: '분할 매수 차수를 1개 이상 켜세요.' }
  for (const [i, b] of buy.entries()) {
    if (b.ratio <= 0 || b.ratio >= 1) {
      return { ok: false, error: `매수 ${i + 1}차 되돌림 비율은 0~1 사이여야 합니다.` }
    }
    if (b.weight <= 0) return { ok: false, error: `매수 ${i + 1}차 비중을 입력하세요.` }
  }
  const buySum = buy.reduce((a, b) => a + b.weight, 0)
  if (buySum > 100) {
    return { ok: false, error: `매수 비중 합이 ${buySum}% — 100%를 넘을 수 없습니다.` }
  }
  const sell = d.sell.filter((s) => s.enabled)
  for (const [i, x] of sell.entries()) {
    if (x.reboundPct <= 0) return { ok: false, error: `매도 ${i + 1}차 반등률을 입력하세요.` }
    if (x.weight <= 0) return { ok: false, error: `매도 ${i + 1}차 비중을 입력하세요.` }
  }
  const sellSum = sell.reduce((a, s) => a + s.weight, 0)
  if (sellSum > 100) {
    return { ok: false, error: `매도 비중 합이 ${sellSum}% — 100%를 넘을 수 없습니다.` }
  }
  return { ok: true, value: true }
}

/** 드래프트 → 저장 형식. 미입력·형식 오류는 한국어 메시지로 돌려준다.
 *
 *  필수는 분할 차수뿐이다. 진입 기법·수량·허용폭을 필수로 걸면 **입력 UI 가 없는 값**
 *  때문에 저장이 영원히 막힌다 (2026-08-05 실제 발생 — 주문조건 카드 삭제 후 수량 검증이
 *  남아 저장 불능). 안 채운 값은 실행 시 예시값으로 채워진다. */
export function toStrategy(d: StrategyDraft, entryDefs: ConditionParamDef[]): ParseResult<Strategy> {
  // 진입 기법은 선택 사항 — 골랐을 때만 파라미터를 검증한다.
  let entry: Strategy['entry']
  if (d.entryKey) {
    if (isFixedDefinition(d.entryKey)) {
      // 전략 1호(피보나치) — 파라미터는 고정 정의라 사용자 입력이 없다(strategyOne.ts 정본).
      // 여기서 카탈로그 필수값을 요구하면 입력칸이 없는 화면에서 저장이 영원히 막힌다
      // (실측 2026-08-06 "전략 저장이 안되잖아"). 값은 요청 시점에 주입한다.
      entry = { key: d.entryKey, params: {} }
    } else {
      const params = parseParams(entryDefs, d.entryParams)
      if (!params.ok) return params
      entry = { key: d.entryKey, params: params.value }
    }
  }

  // 수량은 옛 저장본 호환용 — 값이 있을 때만 담는다 (입력 UI 는 삭제됨).
  let order: Strategy['order']
  if (d.qty.trim()) {
    const qty = toNum(d.qtyType === 'shares' ? '수량' : '금액', d.qty, d.qtyType === 'shares')
    if (!qty.ok) return qty
    order = { priceType: d.priceType, qtyType: d.qtyType, qty: qty.value, credit: d.credit }
  }

  const stages = checkStages(d)
  if (!stages.ok) return stages

  // 호가 오프셋은 0·음수도 유효하다 (0 = 지지/저항선 그대로, 음수 = 아래).
  const buyOff = Number(d.buyTickOffset || '0')
  if (!Number.isInteger(buyOff)) return { ok: false, error: '[매수 호가 오프셋] 정수를 입력하세요.' }
  const sellOff = Number(d.sellTickOffset || '0')
  if (!Number.isInteger(sellOff)) return { ok: false, error: '[매도 호가 오프셋] 정수를 입력하세요.' }
  const minGap = Number(d.buyMinGapPct || '0')
  if (!Number.isFinite(minGap) || minGap < 0 || minGap >= 100) {
    return { ok: false, error: '[차수 사이 최소 간격] 0 이상 100 미만의 숫자를 입력하세요.' }
  }

  let stop: StopRule | undefined
  if (d.stopEnabled) {
    const ticks = Number(d.stopTicks || '0')
    if (!Number.isInteger(ticks)) return { ok: false, error: '[손절 호가 오프셋] 정수를 입력하세요.' }
    if (d.stopMode === 'pct') {
      const pct = toNum('손절 %', d.stopPct, false)
      if (!pct.ok) return pct
      stop = { enabled: true, mode: 'pct', pct: pct.value, source: d.stopSource, tickOffset: ticks }
    } else if (d.stopMode === 'fib') {
      const ratio = Number(d.stopFibRatio)
      if (!FIB_STOP_CHOICES.some((r) => Math.abs(r - ratio) < 1e-9)) {
        return { ok: false, error: '[손절 되돌림 선] 목록에서 고르세요.' }
      }
      stop = {
        enabled: true,
        mode: 'fib',
        source: d.stopSource,
        tickOffset: ticks,
        fibRatio: ratio,
      }
    } else {
      let customPrice: number | undefined
      if (d.stopSource === 'custom') {
        const cp = toNum('손절 기준 가격', d.stopCustom, false)
        if (!cp.ok) return cp
        customPrice = cp.value
      }
      stop = { enabled: true, mode: 'support', source: d.stopSource, customPrice, tickOffset: ticks }
    }
  }

  return {
    ok: true,
    value: {
      screen: {
        ...(d.screenName ? { name: d.screenName } : {}),
        logic: d.logic,
        conditions: d.conditions,
      },
      ...(entry ? { entry } : {}),
      ...(stop ? { stop } : {}),
      reenterSameWave: d.reenterSameWave,
      split: {
        buy: d.buy.map((b) => ({ ...b })),
        sell: d.sell.map((s) => ({ ...s })),
        sellBasis: d.sellBasis,
        buyTickOffset: buyOff,
        sellTickOffset: sellOff,
        buyMinGapPct: minGap,
      },
      ...(order ? { order } : {}),
    },
  }
}
