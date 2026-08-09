// 전략 화면(①~④) 스텝들이 함께 쓰는 상수·헬퍼·소형 컴포넌트 —
// StrategyPanel.tsx 분할(구조 리팩토링 2026-08-06) 때 원본에서 그대로 옮겼다. 값·마크업 불변.

import { useState } from 'react'
import type { ConditionDef, ConditionParamDef, SimulateRequest, Symbol } from '../../../api'
import { SymbolResults } from '../../components/SymbolResults'
import { Chip, Chips, KV } from '../../components/ui'
import { useListCursor, useLiveSearch } from '../../components/useLiveSearch'
import type { SavedCondition, SellBasis, StrategyDraft } from '../strategyStore'

// ③ 시뮬레이션 종목 — **검색으로 아무 종목이나** 고른다 (오너 지시 2026-08-07:
// "내가 예시로 알려줬던 종목들 다 볼 수도 없네"). 아래는 자주 쓰는 바로가기일 뿐이고,
// 고정 목록이 아니다. 성격이 다른 종목들로 골라 뒀다 — 크게 오른 것 / 중간 / 크게 밀린 것.
export const SIM_QUICK_SYMS = [
  { code: '005930', name: '삼성전자', market: 'KOSPI' },
  { code: '000660', name: 'SK하이닉스', market: 'KOSPI' },
  { code: '108490', name: '로보티즈', market: 'KOSDAQ' },
  { code: '005380', name: '현대차', market: 'KOSPI' },
  { code: '009150', name: '삼성전기', market: 'KOSPI' },
] as const

export const SIM_SYM = SIM_QUICK_SYMS[0]

// ③ 예시 기본값 — 지위는 PLACEHOLDER 와 같다 (ADR-0009: 서버 하드코딩 금지, UI 예시는 허용).
// 실행 시 **빈 항목만** 이 값으로 채우고, 채운 값은 전부 화면(분할 카드·메시지)에 보인다.
// "실행 버튼을 누르면 무조건 예시가 보여야 한다"(오너) — 빈 폼 때문에 실행을 막지 않는다.
export const SIM_EXAMPLE = {
  qtyShares: 100,
  buy: [
    { ratio: 0.382, weight: 33 },
    { ratio: 0.5, weight: 33 },
    { ratio: 0.618, weight: 34 },
  ],
} as const

/** date input 기본값 = 오늘 (오너 지시 2026-08-06 — 기준일은 오늘로 통일).
 *  toISOString 은 UTC 라 KST 새벽에 전날이 나온다 — 로컬 달력으로 만든다.
 *  서버는 기준일 이후 데이터를 안 보므로 휴장일이면 자동으로 직전 거래일 기준이 된다. */
export function todayStr(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export const PLACEHOLDER: Record<string, string> = {
  short: '5',
  mid: '20',
  long: '60',
  period: '20',
  days: '250',
  within: '3',
  amount: '300',
  zz_depth: '10',
  zz_deviation: '3',
  sr_prd: '5',
  sr_loopback: '120',
  sr_min_strength: '1',
}

/** 폼 드래프트 → 서버가 받는 손절 설정. ③ 시뮬레이션·④ 백테스팅이 **같은 값**을 보내야
 *  같은 자리를 그린다 — 전에는 두 화면이 각자 만들어서 한쪽만 고치기 쉬웠다.
 *  계산 정본은 서버 `layer4_execution.stops.stop_price` 하나다. */
export function stopPayload(draft: StrategyDraft): SimulateRequest['stop'] {
  if (!draft.stopEnabled) return undefined
  const num = (v: string) => (Number(v) > 0 ? Number(v) : undefined)
  const ticks = Number(draft.stopTicks)
  return {
    enabled: true,
    mode: draft.stopMode,
    pct: num(draft.stopPct),
    source: draft.stopSource,
    custom_price: num(draft.stopCustom),
    tick_offset: Number.isInteger(ticks) ? ticks : 0,
    fib_ratio: num(draft.stopFibRatio),
  }
}

export function rowLabel(i: number): string {
  return i < 26 ? String.fromCharCode(65 + i) : String(i + 1)
}

function fmtVal(v: number | string, unit: string): string {
  // select 값은 "흑자" 같은 말이라 단위도 천단위 구분도 붙이지 않는다.
  return typeof v === 'number' ? `${v.toLocaleString()}${unit}` : String(v)
}

export function summarizeCond(c: SavedCondition, def: ConditionDef | undefined): string {
  if (!def) return c.key
  const parts: string[] = []
  for (const p of def.params) {
    if (p.key === 'min' || p.key === 'max') continue
    const v = c.params[p.key]
    if (v != null) parts.push(`${p.label} ${fmtVal(v, p.unit)}`)
  }
  const minDef = def.params.find((p) => p.key === 'min')
  const maxDef = def.params.find((p) => p.key === 'max')
  const min = c.params['min']
  const max = c.params['max']
  if (min != null && max != null && minDef && maxDef && minDef.unit === maxDef.unit) {
    parts.push(`${fmtVal(min, '')}~${fmtVal(max, maxDef.unit)}`)
  } else {
    if (min != null && minDef) parts.push(`${fmtVal(min, minDef.unit)} ${minDef.label}`)
    if (max != null && maxDef) parts.push(`${fmtVal(max, maxDef.unit)} ${maxDef.label}`)
  }
  return parts.length ? `${def.name} ${parts.join(' · ')}` : def.name
}

export const SELL_BASIS_LABEL = { avg_entry: '매수 평단', lowest_fill: '최저 체결가', anchor_high: '파동 꼭대기' } as const

/** ③ 시뮬 종목 고르기 — 검색으로 아무 종목이나. 자주 쓰는 종목은 아래 바로가기 칩으로.
 *  검색은 헤더 검색창과 **같은 훅·같은 결과 목록**을 쓴다(코드도 동작도 한 벌). */
export function SimSymbolPicker({ value, onPick }: Readonly<{ value: Symbol; onPick: (s: Symbol) => void }>) {
  const [q, setQ] = useState('')
  const { hits, loading } = useLiveSearch(q, {}, 12)
  const { cur, setCur, onKeyDown } = useListCursor(hits.length)
  const term = q.trim()

  function choose(s: Symbol) {
    onPick(s)
    setQ('')
  }

  return (
    <>
      <KV label="종목">
        <input
          style={{ flex: 1 }}
          value={q}
          placeholder={`${value.name} (${value.code})`}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => onKeyDown(e, (i) => hits[i] && choose(hits[i]), () => setQ(''))}
        />
      </KV>
      {term && (
        <div className="sim-sym-hits">
          <SymbolResults
            hits={hits}
            q={term}
            cur={cur}
            onHover={setCur}
            onPick={choose}
            empty={<p className="empty">{loading ? '찾는 중…' : `'${term}' 검색 결과가 없습니다.`}</p>}
          />
        </div>
      )}
      <Chips className="sim-quick">
        {SIM_QUICK_SYMS.map((s) => (
          <Chip key={s.code} on={s.code === value.code} title={s.code} onClick={() => choose({ ...s })}>
            {s.name}
          </Chip>
        ))}
      </Chips>
    </>
  )
}

// 매도 기준점 선택 — ②와 ③ 양쪽에 노출한다. ②에만 묻어두면 ③에서 반등률을 만지는
// 동안 기준점이 뭔지 안 보여 "평단 +10%가 왜 사이클 고점 값이냐"가 재발한다(2026-08-06).
export function SellBasisPicker({ value, onChange }: { value: SellBasis; onChange: (b: SellBasis) => void }) {
  return (
    <KV label="매도 기준점">
      <span className="radios" style={{ marginLeft: 'auto' }}>
        {(Object.entries(SELL_BASIS_LABEL) as [SellBasis, string][]).map(([k, label]) => (
          <label key={k}>
            <input type="radio" checked={value === k} onChange={() => onChange(k)} />
            {label}
          </label>
        ))}
      </span>
    </KV>
  )
}

export function ParamInputs(props: {
  defs: ConditionParamDef[]
  values: Record<string, string>
  onChange: (key: string, v: string) => void
  onEnter?: () => void
}) {
  return (
    <>
      {props.defs.map((p) => (
        <KV label={p.label} key={p.key} desc={p.desc}>
          {p.choices?.length ? (
            <select
              className="amt"
              value={props.values[p.key] ?? p.choices[0]}
              onChange={(e) => props.onChange(p.key, e.target.value)}
            >
              {p.choices.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          ) : (
            <input
              className="amt"
              placeholder={PLACEHOLDER[p.key] ?? ''}
              value={props.values[p.key] ?? ''}
              onChange={(e) => props.onChange(p.key, e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && props.onEnter?.()}
            />
          )}
          <span className="unit">{p.unit}</span>
        </KV>
      ))}
    </>
  )
}
