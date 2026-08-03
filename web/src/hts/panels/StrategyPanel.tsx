import { useEffect, useMemo, useRef, useState } from 'react'
import {
  fetchConditions,
  fetchStrategies,
  runScreen,
  type ConditionCategory,
  type ConditionDef,
  type ConditionParamDef,
  type ScreenResponse,
  type StrategyDef,
} from '../../api'
import { pickStrategy, pickSymbol, type StrategyPick } from '../bus'
import { chgClass, fmtChg, fmtEok, fmtPrice } from '../format'
import {
  deleteStrategy,
  emptyDraft,
  loadStrategies,
  parseParams,
  saveStrategy,
  toDraft,
  toStrategy,
  type SavedCondition,
  type Strategies,
  type StrategyDraft,
  type TargetKind,
} from './strategyStore'

// 전략 = ① 종목 선정(조건검색) + ② 매수 기준(시세포착) + ③ 주문 조건 을 묶은 하나의 정의.
// 정량 값은 전부 이 화면에서 입력·수정한다 (ADR-0009 — 전략 숫자 하드코딩 금지).

const LIMIT = 100

// 입력 힌트일 뿐 기본값이 아니다 — 값은 항상 사용자가 입력한다(서버 기본값 없음).
const PLACEHOLDER: Record<string, string> = {
  short: '5',
  mid: '20',
  long: '60',
  period: '20',
  days: '20',
  within: '3',
  lookback: '250',
  base_window: '20',
  base_range: '8',
  near: '1.5',
}

const TARGET_LABEL: Record<TargetKind, string> = {
  fib: '피보나치 레벨',
  round: '라운드 피겨',
  manual: '직접 입력',
}

const VALID_DAY_PRESETS = [1, 7, 15, 30]

function rowLabel(i: number): string {
  return i < 26 ? String.fromCharCode(65 + i) : String(i + 1)
}

function fmtVal(v: number, unit: string): string {
  return `${v.toLocaleString()}${unit}`
}

function summarizeCond(c: SavedCondition, def: ConditionDef | undefined): string {
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
    parts.push(`${min.toLocaleString()}~${fmtVal(max, maxDef.unit)}`)
  } else {
    if (min != null && minDef) parts.push(`${fmtVal(min, minDef.unit)} ${minDef.label}`)
    if (max != null && maxDef) parts.push(`${fmtVal(max, maxDef.unit)} ${maxDef.label}`)
  }
  return parts.length ? `${def.name} ${parts.join(' · ')}` : def.name
}

const labelStyle = {
  display: 'flex',
  alignItems: 'center',
  gap: 3,
  fontSize: 11,
  color: 'var(--hts-text-2)',
} as const

function Section(props: { title: string; open: boolean; onToggle: () => void; children: React.ReactNode }) {
  return (
    <div style={{ borderTop: '1px solid var(--hts-border)', paddingTop: 6, marginTop: 6 }}>
      <p
        className="panel-title"
        onClick={props.onToggle}
        style={{ cursor: 'pointer', userSelect: 'none', margin: 0 }}
      >
        {props.open ? '▾' : '▸'} {props.title}
      </p>
      {props.open && <div style={{ marginTop: 4 }}>{props.children}</div>}
    </div>
  )
}

/** 카탈로그 스키마 그대로 그리는 파라미터 입력줄 (조건·전략 공용) */
function ParamInputs(props: {
  defs: ConditionParamDef[]
  values: Record<string, string>
  onChange: (key: string, v: string) => void
  onEnter?: () => void
}) {
  return (
    <>
      {props.defs.map((p) => (
        <label key={p.key} style={labelStyle}>
          {p.label}
          <input
            style={{ flex: 'none', width: 64 }}
            placeholder={PLACEHOLDER[p.key] ?? ''}
            value={props.values[p.key] ?? ''}
            onChange={(e) => props.onChange(p.key, e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && props.onEnter?.()}
          />
          {p.unit}
        </label>
      ))}
    </>
  )
}

export function StrategyPanel() {
  // ── 카탈로그 ──
  const [condCats, setCondCats] = useState<ConditionCategory[]>([])
  const [stratCat, setStratCat] = useState<StrategyDef[]>([])
  const [catErr, setCatErr] = useState('')
  const [catReq, setCatReq] = useState(0)

  useEffect(() => {
    let alive = true
    Promise.all([fetchConditions(), fetchStrategies()])
      .then(([c, s]) => {
        if (!alive) return
        setCondCats(c.categories)
        setStratCat(s)
        setCatErr('')
        setCatKey((prev) => prev || (c.categories[0]?.key ?? ''))
      })
      .catch((e: unknown) => {
        if (alive) setCatErr(e instanceof Error ? e.message : '카탈로그 조회 실패')
      })
    return () => {
      alive = false
    }
  }, [catReq])

  const condMap = useMemo(() => {
    const m = new Map<string, ConditionDef>()
    for (const cat of condCats) for (const c of cat.conditions) m.set(c.key, c)
    return m
  }, [condCats])
  const stratMap = useMemo(() => new Map(stratCat.map((s) => [s.key, s])), [stratCat])

  // ── 전략 저장/불러오기 ──
  const [saved, setSaved] = useState<Strategies>(loadStrategies)
  const [name, setName] = useState('')
  const [naming, setNaming] = useState(false) // 인라인 이름 입력 (prompt 창 금지)
  const [nameDraft, setNameDraft] = useState('')
  const [confirmDel, setConfirmDel] = useState(false) // 2단계 확인 (confirm 창 금지)
  const [msg, setMsg] = useState('')

  const [draft, setDraft] = useState<StrategyDraft>(emptyDraft)
  const set = <K extends keyof StrategyDraft>(k: K, v: StrategyDraft[K]) =>
    setDraft((d) => ({ ...d, [k]: v }))

  const [open, setOpen] = useState({ screen: true, entry: true, order: true })
  const toggle = (k: keyof typeof open) => setOpen((o) => ({ ...o, [k]: !o[k] }))

  function loadSaved(n: string) {
    setName(n)
    setConfirmDel(false)
    setNaming(false)
    const s = saved[n]
    if (!s) return
    setDraft(toDraft(s))
    setMsg(`전략 [${n}] 불러옴`)
  }

  function newStrategy() {
    setName('')
    setDraft(emptyDraft())
    setConfirmDel(false)
    setNaming(false)
    setMsg('새 전략 — 값을 입력한 뒤 저장하세요.')
  }

  const entryDef = stratMap.get(draft.entryKey)

  function beginSave() {
    const r = toStrategy(draft, entryDef?.params ?? [])
    if (!r.ok) {
      setMsg(r.error)
      return
    }
    setNameDraft(name)
    setNaming(true)
    setMsg('')
  }

  function doSave() {
    const n = nameDraft.trim()
    if (!n) return
    const r = toStrategy(draft, entryDef?.params ?? [])
    if (!r.ok) {
      setMsg(r.error)
      return
    }
    setSaved(saveStrategy(saved, n, r.value))
    setName(n)
    setNaming(false)
    setMsg(`전략 [${n}] 저장됨`)
  }

  function doDelete() {
    setSaved(deleteStrategy(saved, name))
    setMsg(`전략 [${name}] 삭제됨`)
    setName('')
    setDraft(emptyDraft())
    setConfirmDel(false)
  }

  // ── ① 종목 선정 ──
  const [catKey, setCatKey] = useState('')
  const [condKey, setCondKey] = useState('')
  const [condDraft, setCondDraft] = useState<Record<string, string>>({})
  const [screenErr, setScreenErr] = useState('')
  const condDef = condMap.get(condKey)
  const catConds = condCats.find((c) => c.key === catKey)?.conditions ?? []

  function addCond() {
    if (!condDef) {
      setScreenErr('조건을 선택하세요.')
      return
    }
    const r = parseParams(condDef.params, condDraft)
    if (!r.ok) {
      setScreenErr(r.error)
      return
    }
    setDraft((d) => ({ ...d, conditions: [...d.conditions, { key: condDef.key, params: r.value }] }))
    setCondDraft({})
    setScreenErr('')
  }

  function removeCond(i: number) {
    setDraft((d) => ({ ...d, conditions: d.conditions.filter((_, idx) => idx !== i) }))
  }

  const [date, setDate] = useState('')
  const [result, setResult] = useState<ScreenResponse | null>(null)
  const [runMsg, setRunMsg] = useState('')
  const [running, setRunning] = useState(false)
  const runReq = useRef(0)

  async function run() {
    if (draft.conditions.length === 0) {
      setRunMsg('조건을 1개 이상 추가한 뒤 검색하세요.')
      return
    }
    const req = ++runReq.current
    setRunning(true)
    setRunMsg('조회 중…')
    try {
      const r = await runScreen({
        date: date || undefined,
        logic: draft.logic,
        conditions: draft.conditions,
        limit: LIMIT,
      })
      if (req !== runReq.current) return
      setResult(r)
      setRunMsg(
        r.items.length === 0
          ? `${r.date} 기준 조건에 맞는 종목이 없습니다.`
          : `${r.date} 기준 ${r.total}종목 (상위 ${r.items.length})`,
      )
    } catch (e) {
      if (req !== runReq.current) return
      setResult(null) // 에러 밑에 이전 결과가 남으면 조건과 결과가 어긋나 보인다
      setRunMsg(e instanceof Error ? e.message : '조회 실패')
    } finally {
      if (req === runReq.current) setRunning(false)
    }
  }

  // ── ② 매수 기준 ──
  const [entryErr, setEntryErr] = useState('')

  function showOnChart() {
    if (!entryDef) {
      setEntryErr('전략을 선택하세요.')
      return
    }
    const r = parseParams(entryDef.params, draft.entryParams)
    if (!r.ok) {
      setEntryErr(r.error)
      return
    }
    setEntryErr('')
    const pick: StrategyPick = {
      key: entryDef.key,
      params: r.value,
      signals: entryDef.signals,
      overlay: entryDef.overlay,
    }
    pickStrategy(pick)
  }

  return (
    <div className="panel-col">
      <div className="toolbar">
        <select value={name} onChange={(e) => loadSaved(e.target.value)} title="저장된 전략 불러오기">
          <option value="">내 전략…</option>
          {Object.keys(saved).map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
        {naming ? (
          <>
            <input
              size={9}
              autoFocus
              placeholder="전략 이름"
              value={nameDraft}
              onChange={(e) => setNameDraft(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && doSave()}
            />
            <button onClick={doSave}>확인</button>
            <button onClick={() => setNaming(false)}>취소</button>
          </>
        ) : confirmDel ? (
          <>
            <button onClick={doDelete} title={`전략 [${name}] 제거`}>
              정말 삭제
            </button>
            <button onClick={() => setConfirmDel(false)}>취소</button>
          </>
        ) : (
          <>
            <button onClick={beginSave}>저장</button>
            <button disabled={!name} onClick={() => setConfirmDel(true)}>
              삭제
            </button>
            <button onClick={newStrategy}>새 전략</button>
          </>
        )}
      </div>

      <div className="panel-body">
        {catErr && (
          <p className="hint">
            {catErr} <button onClick={() => setCatReq((n) => n + 1)}>다시 시도</button>
          </p>
        )}
        {msg && <p className="hint">{msg}</p>}

        {/* ① 종목 선정 — 조건검색식이 전략 안에 들어간다 */}
        <Section title="① 종목 선정 (조건검색)" open={open.screen} onToggle={() => toggle('screen')}>
          <div className="form-row" style={{ flexWrap: 'wrap', alignItems: 'center' }}>
            <select
              value={catKey}
              onChange={(e) => {
                setCatKey(e.target.value)
                setCondKey('')
                setCondDraft({})
              }}
              title="조건 카테고리"
            >
              <option value="">카테고리…</option>
              {condCats.map((c) => (
                <option key={c.key} value={c.key}>
                  {c.name}
                </option>
              ))}
            </select>
            <select
              value={condKey}
              onChange={(e) => {
                setCondKey(e.target.value)
                setCondDraft({})
                setScreenErr('')
              }}
              title="조건 선택"
            >
              <option value="">조건…</option>
              {catConds.map((c) => (
                <option key={c.key} value={c.key}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>
          {condDef && (
            <>
              <p className="hint">{condDef.desc} · 이평·지표는 종가 기준</p>
              <div className="form-row" style={{ flexWrap: 'wrap', alignItems: 'center' }}>
                <ParamInputs
                  defs={condDef.params}
                  values={condDraft}
                  onChange={(k, v) => setCondDraft({ ...condDraft, [k]: v })}
                  onEnter={addCond}
                />
                <button onClick={addCond}>조건 추가</button>
              </div>
            </>
          )}
          {screenErr && <p className="hint">{screenErr}</p>}

          {draft.conditions.length > 0 && (
            <table className="grid">
              <tbody>
                {draft.conditions.map((c, i) => (
                  <tr key={`${c.key}-${i}`}>
                    <td className="flat" style={{ width: 22 }}>
                      {rowLabel(i)}
                    </td>
                    <td>{summarizeCond(c, condMap.get(c.key))}</td>
                    <td className="num" style={{ width: 30 }}>
                      <button title="조건 삭제" onClick={() => removeCond(i)}>
                        ×
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <div style={{ display: 'flex', alignItems: 'center', gap: 4, margin: '6px 0' }}>
            <select
              value={draft.logic}
              onChange={(e) => set('logic', e.target.value as 'and' | 'or')}
              title="조건 결합 방식"
            >
              <option value="and">전체 AND</option>
              <option value="or">전체 OR</option>
            </select>
            <input
              type="date"
              style={{ flex: 'none', width: 120 }}
              value={date}
              onChange={(e) => setDate(e.target.value)}
              title="기준일 (빈칸 = 최신 거래일)"
            />
            <button className="primary" disabled={running} onClick={() => void run()}>
              {running ? '조회 중…' : '종목 검색'}
            </button>
          </div>
          <p className="hint">{runMsg || '결합은 전체 AND / 전체 OR 만 지원합니다 (괄호 조합 미지원).'}</p>

          {result && result.items.length > 0 && (
            <table className="grid">
              <thead>
                <tr>
                  <th>종목명</th>
                  <th className="num">현재가</th>
                  <th className="num">등락률</th>
                  <th className="num">거래대금</th>
                </tr>
              </thead>
              <tbody>
                {result.items.map((it) => (
                  <tr
                    key={it.code}
                    title="클릭 시 차트 종목 전환"
                    onClick={() => pickSymbol({ code: it.code, name: it.name, market: it.market })}
                  >
                    <td>{it.name}</td>
                    <td className={`num ${chgClass(it.chg)}`}>{fmtPrice(it.close)}</td>
                    <td className={`num ${chgClass(it.chg)}`}>{fmtChg(it.chg)}</td>
                    <td className="num">{fmtEok(it.amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>

        {/* ② 매수 기준 (시세포착) */}
        <Section title="② 매수 기준 (시세포착)" open={open.entry} onToggle={() => toggle('entry')}>
          <div className="form-row">
            <select
              value={draft.entryKey}
              onChange={(e) => {
                set('entryKey', e.target.value)
                set('entryParams', {})
                setEntryErr('')
              }}
              style={{ flex: 1 }}
            >
              <option value="">전략 선택…</option>
              {stratCat.map((s) => (
                <option key={s.key} value={s.key}>
                  {s.name}
                </option>
              ))}
            </select>
          </div>
          {entryDef ? (
            <>
              <p className="hint">{entryDef.desc}</p>
              <div className="form-row" style={{ flexWrap: 'wrap', alignItems: 'center' }}>
                <ParamInputs
                  defs={entryDef.params}
                  values={draft.entryParams}
                  onChange={(k, v) => set('entryParams', { ...draft.entryParams, [k]: v })}
                />
              </div>
              {(entryDef.overlay || entryDef.signals) && (
                <div style={{ display: 'flex', gap: 4, margin: '6px 0' }}>
                  <button onClick={showOnChart}>차트에 표시</button>
                  <button onClick={() => pickStrategy(null)}>해제</button>
                </div>
              )}
            </>
          ) : (
            <p className="hint">전략을 선택하면 파라미터 입력 폼이 표시됩니다.</p>
          )}
          {entryErr && <p className="hint">{entryErr}</p>}

          <p className="panel-title" style={{ margin: '8px 0 2px' }}>
            감시 조건
          </p>
          <div className="form-row" style={{ flexWrap: 'wrap', alignItems: 'center' }}>
            <label style={labelStyle}>
              목표가 기준
              <select value={draft.target} onChange={(e) => set('target', e.target.value as TargetKind)}>
                {(Object.keys(TARGET_LABEL) as TargetKind[]).map((t) => (
                  <option key={t} value={t}>
                    {TARGET_LABEL[t]}
                  </option>
                ))}
              </select>
            </label>
            <label style={labelStyle}>
              근접 허용 오차
              <input
                style={{ flex: 'none', width: 64 }}
                placeholder={PLACEHOLDER.near}
                value={draft.near}
                onChange={(e) => set('near', e.target.value)}
              />
              %
            </label>
            {draft.target === 'manual' && (
              <label style={labelStyle}>
                목표가
                <input
                  style={{ flex: 'none', width: 80 }}
                  value={draft.manualPrice}
                  onChange={(e) => set('manualPrice', e.target.value)}
                />
                원
              </label>
            )}
          </div>
          <p className="hint">목표가에 근접하면 감시가 걸립니다 — 판단·계산은 전부 서버(파이썬)가 합니다.</p>
        </Section>

        {/* ③ 주문 조건 */}
        <Section title="③ 매수 주문조건 설정" open={open.order} onToggle={() => toggle('order')}>
          <div className="form-row" style={{ flexWrap: 'wrap', alignItems: 'center' }}>
            <span style={labelStyle}>주문 구분</span>
            <label style={labelStyle}>
              <input
                type="radio"
                style={{ flex: 'none', width: 'auto' }}
                checked={draft.priceType === 'market'}
                onChange={() => set('priceType', 'market')}
              />
              시장가
            </label>
            <label style={labelStyle}>
              <input
                type="radio"
                style={{ flex: 'none', width: 'auto' }}
                checked={draft.priceType === 'limit'}
                onChange={() => set('priceType', 'limit')}
              />
              지정가
            </label>
          </div>

          <div className="form-row" style={{ flexWrap: 'wrap', alignItems: 'center' }}>
            <label style={labelStyle}>
              <input
                type="radio"
                style={{ flex: 'none', width: 'auto' }}
                checked={draft.qtyType === 'shares'}
                onChange={() => set('qtyType', 'shares')}
              />
              수량
            </label>
            <label style={labelStyle}>
              <input
                type="radio"
                style={{ flex: 'none', width: 'auto' }}
                checked={draft.qtyType === 'amount'}
                onChange={() => set('qtyType', 'amount')}
              />
              금액
            </label>
            <input
              style={{ flex: 'none', width: 96 }}
              value={draft.qty}
              onChange={(e) => set('qty', e.target.value)}
              title={draft.qtyType === 'shares' ? '매수 수량(주)' : '매수 금액(원)'}
            />
            <span style={labelStyle}>{draft.qtyType === 'shares' ? '주' : '원'}</span>
          </div>

          <div className="form-row" style={{ flexWrap: 'wrap', alignItems: 'center' }}>
            <span style={labelStyle}>감시 유효기간</span>
            {VALID_DAY_PRESETS.map((d) => (
              <button key={d} onClick={() => set('validDays', String(d))}>
                {d}일
              </button>
            ))}
            <input
              style={{ flex: 'none', width: 64 }}
              value={draft.validDays}
              onChange={(e) => set('validDays', e.target.value)}
              title="감시 유효기간(일) 직접 입력"
            />
            <span style={labelStyle}>일</span>
          </div>

          <p className="hint">주문은 아직 나가지 않습니다 — 전략 정의 저장용.</p>
        </Section>
      </div>
    </div>
  )
}
