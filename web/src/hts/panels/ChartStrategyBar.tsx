import { useEffect, useMemo, useState } from 'react'
import { fetchStrategies, type StrategyDef } from '../../api'
import { pickStrategy, type StrategyPick } from '../bus'
import { PLACEHOLDER, ParamInputs } from './strategy/common'
import { STRATEGY_ONE_PARAMS } from './strategyOne'

// 차트에 전략을 얹는 자리 — **여기가 없어서** 지지저항을 보려면 ③ 시뮬레이션으로 들어가야
// 했다 (오너 지적 2026-08-08: "또 시뮬레이션 탭에 다 때려 넣고 있는 거 아니지?").
// 서버 카탈로그(/api/strategies)를 그대로 폼으로 그린다 — ① 종목선정과 **같은 폼 코드**
// (ParamInputs)를 쓰므로 전략을 새로 추가해도 화면 코드를 안 고쳐도 된다.
//
// 기준일은 차트 오른쪽 끝 봉이다(ProChart 가 알아서 따라간다). 그 오른쪽은 서버가 안 본다.

/** 파라미터 칸의 처음 값. 전략 1호 정의 → 조건검색 예시값 → 드롭다운 첫 항목 순으로 채운다. */
function defaultsFor(def: StrategyDef): Record<string, string> {
  const one = STRATEGY_ONE_PARAMS as Record<string, string | number>
  const out: Record<string, string> = {}
  for (const p of def.params) {
    const fixed = one[p.key]
    if (fixed != null) out[p.key] = String(fixed)
    else if (p.choices?.length) out[p.key] = p.choices[0]
    else if (PLACEHOLDER[p.key]) out[p.key] = PLACEHOLDER[p.key]
  }
  return out
}

/** 폼 값(문자열) → 서버 params. select 는 말 그대로, 나머지는 숫자로. */
function toParams(def: StrategyDef, vals: Record<string, string>): Record<string, number | string> {
  const out: Record<string, number | string> = {}
  for (const p of def.params) {
    const v = vals[p.key]
    if (v == null || v === '') continue
    out[p.key] = p.choices?.length ? v : Number(v)
  }
  return out
}

export function ChartStrategyBar() {
  const [defs, setDefs] = useState<StrategyDef[]>([])
  const [err, setErr] = useState('')
  const [key, setKey] = useState('')
  const [vals, setVals] = useState<Record<string, string>>({})
  const [open, setOpen] = useState(false)

  useEffect(() => {
    let alive = true
    fetchStrategies()
      .then((s) => alive && setDefs(s.filter((d) => d.overlay || d.signals)))
      .catch((e: unknown) => alive && setErr(e instanceof Error ? e.message : '전략 목록 조회 실패'))
    return () => {
      alive = false
    }
  }, [])

  const def = useMemo(() => defs.find((d) => d.key === key) ?? null, [defs, key])

  function choose(nextKey: string) {
    setKey(nextKey)
    const d = defs.find((x) => x.key === nextKey)
    if (!d) {
      pickStrategy(null) // 해제 — 차트가 오버레이를 지운다
      setVals({})
      return
    }
    const v = defaultsFor(d)
    setVals(v)
    apply(d, v)
  }

  function apply(d: StrategyDef, v: Record<string, string>) {
    const pick: StrategyPick = {
      key: d.key,
      params: toParams(d, v),
      signals: d.signals,
      overlay: d.overlay,
    }
    pickStrategy(pick)
  }

  return (
    <div className="chart-strategy">
      <select value={key} onChange={(e) => choose(e.target.value)} title="차트에 얹을 전략">
        <option value="">전략 없음</option>
        {defs.map((d) => (
          <option key={d.key} value={d.key}>
            {d.name}
          </option>
        ))}
      </select>
      {def && (
        <>
          <button className={open ? 'on' : ''} onClick={() => setOpen((o) => !o)}>
            값 {open ? '▴' : '▾'}
          </button>
          <button className="primary" onClick={() => apply(def, vals)}>
            다시 그리기
          </button>
          <span className="dim">{def.desc}</span>
        </>
      )}
      {err && <span className="hint warn">{err}</span>}
      {def && open && (
        <div className="chart-strategy-params">
          <ParamInputs
            defs={def.params}
            values={vals}
            onChange={(k, v) => setVals((s) => ({ ...s, [k]: v }))}
            onEnter={() => apply(def, vals)}
          />
        </div>
      )}
    </div>
  )
}
