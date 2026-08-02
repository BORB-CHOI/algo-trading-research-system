import { useEffect, useMemo, useState } from 'react'
import { fetchStrategies, type StrategyDef } from '../../api'
import { currentStrategy, pickStrategy, type StrategyPick } from '../bus'

// 전략 패널 — 조건검색과 같은 방식: 서버 카탈로그(이름·설명·파라미터 스키마)를 받아
// 오너가 파라미터를 보고 직접 입력·수정한다(ADR-0009 — 전략 숫자 하드코딩 금지).
// 신호/오버레이 계산은 전부 파이썬 — 여기는 입력 UI 와 payload 전파만 한다.

// 파라미터 placeholder — 관례값 힌트일 뿐, 값은 항상 사용자가 입력한다(서버 기본값 없음).
const PLACEHOLDER: Record<string, string> = {
  short: '5',
  long: '20',
  lookback: '250',
  base_window: '20',
  base_range: '8',
  near: '1.5',
}

/** 배지 요약: "피보나치 되돌림 (전략 1호) · 탐색 구간 250일 · …" */
function summarize(p: StrategyPick, def: StrategyDef | undefined): string {
  if (!def) return p.key // 카탈로그 로드 전/모르는 key — key 라도 보여준다
  const parts = def.params
    .filter((d) => p.params[d.key] != null)
    .map((d) => `${d.label} ${p.params[d.key].toLocaleString()}${d.unit}`)
  return parts.length ? `${def.name} · ${parts.join(' · ')}` : def.name
}

export function StrategyPanel() {
  // ── 전략 카탈로그 (GET /api/strategies) ──
  const [catalog, setCatalog] = useState<StrategyDef[]>([])
  const [catErr, setCatErr] = useState('')
  const [catReq, setCatReq] = useState(0) // 재시도 트리거

  useEffect(() => {
    let alive = true
    fetchStrategies()
      .then((list) => {
        if (!alive) return
        setCatalog(list)
        setCatErr('')
      })
      .catch((e: unknown) => {
        if (alive) setCatErr(e instanceof Error ? e.message : '전략 목록 조회 실패')
      })
    return () => {
      alive = false
    }
  }, [catReq])

  const defMap = useMemo(() => new Map(catalog.map((s) => [s.key, s])), [catalog])

  // ── 선택·파라미터 입력·적용 상태 ──
  const [selKey, setSelKey] = useState('')
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [formErr, setFormErr] = useState('')
  // 마운트(추가·레이아웃 복원) 시 bus 의 마지막 적용 상태를 이어받아 배지를 유지한다.
  const [applied, setApplied] = useState<StrategyPick | null>(() => currentStrategy())

  const selDef = defMap.get(selKey)

  function select(key: string) {
    setSelKey(key)
    setDraft({})
    setFormErr('')
  }

  // 조건검색(ScreenPanel)과 같은 검증 규칙 — 미입력·비숫자·정수 위반을 한국어로 알린다.
  function apply() {
    if (!selDef) return
    const params: Record<string, number> = {}
    for (const p of selDef.params) {
      const raw = (draft[p.key] ?? '').trim()
      if (!raw) {
        if (p.required) {
          setFormErr(`[${p.label}] 값을 입력하세요.`)
          return
        }
        continue
      }
      const v = Number(raw)
      if (!Number.isFinite(v)) {
        setFormErr(`[${p.label}] 숫자를 입력하세요.`)
        return
      }
      if (p.type === 'int' && !Number.isInteger(v)) {
        setFormErr(`[${p.label}] 정수를 입력하세요.`)
        return
      }
      params[p.key] = v
    }
    setFormErr('')
    const pick: StrategyPick = {
      key: selDef.key,
      params,
      signals: selDef.signals,
      overlay: selDef.overlay,
    }
    setApplied(pick)
    pickStrategy(pick) // 모든 차트 패널에 전파
  }

  function clear() {
    setApplied(null)
    pickStrategy(null) // null = 해제
  }

  return (
    <div className="panel-col">
      {/* 상단 툴바 — 제목 + 적용 상태 배지(파라미터 요약은 title 툴팁) */}
      <div className="toolbar">
        <span className="panel-title" style={{ margin: 0 }}>전략</span>
        {applied && (
          <span
            className="badge"
            style={{ marginLeft: 'auto' }}
            title={summarize(applied, defMap.get(applied.key))}
          >
            적용중: {defMap.get(applied.key)?.name ?? applied.key}
          </span>
        )}
      </div>

      <div className="panel-body">
        {catErr && (
          <p className="hint">
            {catErr}{' '}
            <button onClick={() => setCatReq((n) => n + 1)}>다시 시도</button>
          </p>
        )}

        {/* 전략 선택 */}
        <select value={selKey} onChange={(e) => select(e.target.value)} style={{ width: '100%' }}>
          <option value="">전략 선택…</option>
          {catalog.map((s) => (
            <option key={s.key} value={s.key}>
              {s.name}
            </option>
          ))}
        </select>

        {/* 파라미터 입력 폼 — 조건검색과 동일한 스키마·폼 패턴 */}
        {selDef ? (
          <>
            <p className="hint">{selDef.desc}</p>
            <div className="form-row" style={{ flexWrap: 'wrap', alignItems: 'center' }}>
              {selDef.params.map((p) => (
                <label
                  key={p.key}
                  style={{ display: 'flex', alignItems: 'center', gap: 3, fontSize: 11, color: 'var(--hts-text-2)' }}
                >
                  {p.label}
                  <input
                    style={{ flex: 'none', width: 64 }}
                    placeholder={PLACEHOLDER[p.key] ?? ''}
                    value={draft[p.key] ?? ''}
                    onChange={(e) => setDraft({ ...draft, [p.key]: e.target.value })}
                    onKeyDown={(e) => e.key === 'Enter' && apply()}
                  />
                  {p.unit}
                </label>
              ))}
            </div>
            {/* form-row 는 버튼을 전부 액센트로 칠하므로 적용/해제는 일반 줄에 둔다 */}
            <div style={{ display: 'flex', gap: 4, margin: '6px 0' }}>
              <button className="primary" onClick={apply}>
                차트에 적용
              </button>
              <button disabled={!applied} onClick={clear}>
                해제
              </button>
            </div>
          </>
        ) : (
          <p className="hint">전략을 선택하면 파라미터 입력 폼이 표시됩니다.</p>
        )}
        {formErr && <p className="hint">{formErr}</p>}

        <p className="hint">
          신호(▲매수/▼매도)·오버레이(피보나치 수평선)는 파이썬이 계산한 시각화일 뿐 매매 판단이
          아닙니다. 예시 전략은 확정 전략이 아닙니다.
        </p>
      </div>
    </div>
  )
}
