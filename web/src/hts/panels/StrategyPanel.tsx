import { useEffect, useMemo, useRef, useState } from 'react'
import {
  fetchConditions,
  fetchQuotes,
  fetchStrategies,
  runScreen,
  type ConditionCategory,
  type ConditionDef,
  type ConditionParamDef,
  type Quote,
  type ScreenResponse,
  type StrategyDef,
} from '../../api'
import { currentSymbol, onSymbolPick, pickStrategy, pickSymbol, type StrategyPick, type SymbolPick } from '../bus'
import { chgClass, fmtChg, fmtEok, fmtPrice } from '../format'
import { Sparkline } from '../Sparkline'
import {
  QUICK_CONDITIONS,
  deleteScreen,
  loadScreens,
  saveScreen,
  type ScreenDef,
  type Screens,
} from './screenStore'
import {
  deleteStrategy,
  emptyDraft,
  loadStrategies,
  parseParams,
  saveStrategy,
  toDraft,
  toStrategy,
  type CreditType,
  type SavedCondition,
  type Strategies,
  type StrategyDraft,
} from './strategyStore'

// 전략 화면은 2단계다.
//  ① 종목선정 — 조건검색식을 여러 개 만들고 고친다 (저장소: hts-screens)
//  ② 매매전략 — 그중 하나를 골라 진입기법·주문조건을 붙인다 (저장소: hts-strategies)
// 시세포착 감시는 제거했다 (ADR-0008 개정 4) — 목표가는 진입 기법이 계산할 몫이다.
// 정량 값은 전부 이 화면에서 입력한다 (ADR-0009 — 전략 숫자 하드코딩 금지).

const LIMIT = 100

const PLACEHOLDER: Record<string, string> = {
  short: '5',
  mid: '20',
  long: '60',
  period: '20',
  days: '250',
  within: '3',
  lookback: '250',
  base_window: '20',
  base_range: '8',
  near: '1.5',
}

type Step = 'screen' | 'strategy'

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

function Card(props: {
  title: string
  sub?: string
  right?: React.ReactNode
  flush?: boolean
  children: React.ReactNode
}) {
  return (
    <section className="card">
      <div className="hd">
        {props.title}
        {props.sub && <span className="sub">{props.sub}</span>}
        {props.right && <span className="right">{props.right}</span>}
      </div>
      <div className={`bd ${props.flush ? 'flush' : ''}`}>{props.children}</div>
    </section>
  )
}

function ParamInputs(props: {
  defs: ConditionParamDef[]
  values: Record<string, string>
  onChange: (key: string, v: string) => void
  onEnter?: () => void
}) {
  return (
    <>
      {props.defs.map((p) => (
        <div className="kv" key={p.key}>
          <span className="k">{p.label}</span>
          <span className="v">
            <input
              className="amt"
              placeholder={PLACEHOLDER[p.key] ?? ''}
              value={props.values[p.key] ?? ''}
              onChange={(e) => props.onChange(p.key, e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && props.onEnter?.()}
            />
            <span className="unit">{p.unit}</span>
          </span>
        </div>
      ))}
    </>
  )
}

function SymbolDetail({ sym }: { sym: SymbolPick | null }) {
  const [q, setQ] = useState<Quote | null>(null)
  const req = useRef(0)

  useEffect(() => {
    const code = sym?.code
    if (!code) {
      setQ(null)
      return
    }
    const id = ++req.current
    void fetchQuotes([code], true).then((r) => {
      if (id === req.current) setQ(r.quotes[0] ?? null)
    })
  }, [sym?.code])

  if (!sym) return <p className="hint">차트·검색 결과에서 종목을 고르면 여기에 표시됩니다.</p>
  const diff = q && q.chg != null ? (q.close * q.chg) / (100 + q.chg) : null
  return (
    <>
      <table className="grid">
        <thead>
          <tr>
            <th>종목명</th>
            <th className="num">현재가</th>
            <th className="num">등락</th>
            <th className="num">거래량</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td className="nm">{sym.name}</td>
            <td className={`num ${chgClass(q?.chg)}`} style={{ fontWeight: 700 }}>
              {q ? fmtPrice(q.close) : '-'}
            </td>
            <td className={`num ${chgClass(q?.chg)}`}>
              {diff != null && `${diff > 0 ? '▲' : '▼'} ${fmtPrice(Math.abs(diff))}`}
              <br />
              {fmtChg(q?.chg)}
            </td>
            <td className="num">{q?.volume != null ? Math.round(q.volume).toLocaleString() : '-'}</td>
          </tr>
        </tbody>
      </table>
      <div style={{ marginTop: 10 }}>
        <Sparkline data={q?.spark} width={520} height={64} dot />
      </div>
    </>
  )
}

export function StrategyPanel() {
  const [step, setStep] = useState<Step>('screen')

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

  const [sym, setSym] = useState<SymbolPick | null>(currentSymbol)
  useEffect(() => onSymbolPick(setSym), [])

  // ── ① 조건검색식 ──
  const [screens, setScreens] = useState<Screens>(loadScreens)
  const [screenName, setScreenName] = useState('')
  const [logic, setLogic] = useState<'and' | 'or'>('and')
  const [conds, setConds] = useState<SavedCondition[]>([])
  const [catKey, setCatKey] = useState('')
  const [condKey, setCondKey] = useState('')
  const [condDraft, setCondDraft] = useState<Record<string, string>>({})
  const [screenErr, setScreenErr] = useState('')
  const [screenMsg, setScreenMsg] = useState('')
  const [namingScreen, setNamingScreen] = useState(false)
  const [screenNameDraft, setScreenNameDraft] = useState('')
  const [justAdded, setJustAdded] = useState<number | null>(null)

  const condDef = condMap.get(condKey)
  const catConds = condCats.find((c) => c.key === catKey)?.conditions ?? []

  // 조건마다 입력칸 개수가 달라 고를 때마다 아래가 위아래로 튀었다.
  // 가장 많은 조건 기준으로 자리를 미리 잡아 요동을 없앤다.
  const maxParams = useMemo(() => {
    const counts = condCats.flatMap((c) => c.conditions.map((x) => x.params.length))
    return counts.length ? Math.max(...counts) : 2
  }, [condCats])

  function pickCondition(key: string) {
    const def = condMap.get(key)
    if (!def) return
    for (const cat of condCats) {
      if (cat.conditions.some((c) => c.key === key)) setCatKey(cat.key)
    }
    setCondKey(key)
    setCondDraft({})
    setScreenErr('')
  }

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
    setConds((cs) => {
      setJustAdded(cs.length) // 추가된 줄을 잠깐 강조 — 눌렀는지 아닌지 모르는 걸 막는다
      return [...cs, { key: condDef.key, params: r.value }]
    })
    setCondDraft({})
    setScreenErr('')
  }

  // 강조는 잠깐만. 남겨두면 "선택된 줄"로 오해된다.
  useEffect(() => {
    if (justAdded == null) return
    const t = window.setTimeout(() => setJustAdded(null), 1100)
    return () => window.clearTimeout(t)
  }, [justAdded])

  function loadScreen(name: string) {
    setScreenName(name)
    const s = screens[name]
    if (!s) return
    setLogic(s.logic)
    setConds(s.conditions.map((c) => ({ key: c.key, params: { ...c.params } })))
    setScreenMsg(`검색식 [${name}] 불러옴`)
  }

  function doSaveScreen() {
    const n = screenNameDraft.trim()
    if (!n) return
    setScreens(saveScreen(screens, n, { logic, conditions: conds }))
    setScreenName(n)
    setNamingScreen(false)
    setScreenMsg(`검색식 [${n}] 저장됨`)
  }

  function newScreen() {
    setScreenName('')
    setConds([])
    setLogic('and')
    setScreenMsg('새 검색식 — 조건을 추가하고 저장하세요.')
  }

  const [date, setDate] = useState('')
  const [result, setResult] = useState<ScreenResponse | null>(null)
  const [runMsg, setRunMsg] = useState('')
  const [running, setRunning] = useState(false)
  const runReq = useRef(0)

  async function run(useConds: SavedCondition[], useLogic: 'and' | 'or') {
    const req = ++runReq.current
    setRunning(true)
    setRunMsg('조회 중…')
    try {
      const r = await runScreen({ date: date || undefined, logic: useLogic, conditions: useConds, limit: LIMIT })
      if (req !== runReq.current) return
      setResult(r)
      setRunMsg('')
    } catch (e) {
      if (req !== runReq.current) return
      setResult(null)
      setRunMsg(e instanceof Error ? e.message : '조회 실패')
    } finally {
      if (req === runReq.current) setRunning(false)
    }
  }

  // ── ② 전략 ──
  const [saved, setSaved] = useState<Strategies>(loadStrategies)
  const [name, setName] = useState('')
  const [naming, setNaming] = useState(false)
  const [nameDraft, setNameDraft] = useState('')
  const [confirmDel, setConfirmDel] = useState(false)
  const [msg, setMsg] = useState('')
  const [draft, setDraft] = useState<StrategyDraft>(emptyDraft)
  const [entryErr, setEntryErr] = useState('')
  const set = <K extends keyof StrategyDraft>(k: K, v: StrategyDraft[K]) =>
    setDraft((d) => ({ ...d, [k]: v }))

  const entryDef = stratMap.get(draft.entryKey)

  /** ①에서 만든 검색식을 전략의 종목선정으로 끌어온다 */
  function attachScreen(n: string) {
    const s: ScreenDef | undefined = screens[n]
    if (!s) {
      setDraft((d) => ({ ...d, screenName: '', conditions: [], logic: 'and' }))
      return
    }
    setDraft((d) => ({
      ...d,
      screenName: n,
      logic: s.logic,
      conditions: s.conditions.map((c) => ({ key: c.key, params: { ...c.params } })),
    }))
    setMsg(`[${n}] 검색식을 전략에 붙였습니다 (조건 ${s.conditions.length}개).`)
  }

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
    setMsg('새 전략 — 검색식을 고르고 값을 채우세요.')
  }

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

  function showOnChart() {
    if (!entryDef) {
      setEntryErr('진입 기법을 선택하세요.')
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
      <div className="steps">
        {(
          [
            ['screen', '① 종목선정', `검색식 ${Object.keys(screens).length}`],
            ['strategy', '② 매매전략', `전략 ${Object.keys(saved).length}`],
          ] as const
        ).map(([k, label, badge]) => (
          <button key={k} className={step === k ? 'on' : ''} onClick={() => setStep(k)}>
            {label}
            <span className="badge">{badge}</span>
          </button>
        ))}
      </div>

      <div className="panel-body">
        {catErr && (
          <p className="hint warn">
            {catErr} <button onClick={() => setCatReq((n) => n + 1)}>다시 시도</button>
          </p>
        )}

        {/* ─────────────── ① 종목선정 ─────────────── */}
        {step === 'screen' && (
          <div className="split">
            {/* ── 왼쪽: 조건을 만드는 작업대 ── */}
            <div className="split-a">
              <Card title="조건 만들기" sub="고르고 값을 채운 뒤 추가">
                <div className="chips" style={{ marginBottom: 10 }}>
                  {QUICK_CONDITIONS.filter((q) => condMap.has(q.key)).map((q) => (
                    <button
                      key={q.key}
                      className={`chip ${condKey === q.key ? 'on' : ''}`}
                      title={q.hint ?? condMap.get(q.key)?.desc}
                      onClick={() => pickCondition(q.key)}
                    >
                      {q.label}
                    </button>
                  ))}
                </div>

                <div className="form-row">
                  <select
                    value={catKey}
                    onChange={(e) => {
                      setCatKey(e.target.value)
                      setCondKey('')
                      setCondDraft({})
                    }}
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
                  >
                    <option value="">조건…</option>
                    {catConds.map((c) => (
                      <option key={c.key} value={c.key}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                </div>

                {/* 자리를 미리 잡아둔다 — 조건을 바꿔도 아래가 안 움직인다 */}
                <div className="paramzone" style={{ '--rows': maxParams } as React.CSSProperties}>
                  {condDef ? (
                    <>
                      <p className="hint">
                        {condDef.desc}
                        {condKey === 'new_high' && ' · 52주 ≈ 250거래일 (최대 260)'}
                      </p>
                      <ParamInputs
                        defs={condDef.params}
                        values={condDraft}
                        onChange={(k, v) => setCondDraft({ ...condDraft, [k]: v })}
                        onEnter={addCond}
                      />
                    </>
                  ) : (
                    <p className="hint">위에서 조건을 고르면 입력할 값이 나옵니다.</p>
                  )}
                </div>

                <div className="form-row" style={{ marginTop: 8 }}>
                  <button className="primary" disabled={!condDef} onClick={addCond}>
                    검색식에 추가
                  </button>
                </div>
                {/* 항상 한 줄을 차지한다 — 에러가 뜰 때만 생기면 화면이 튄다 */}
                <p className={`msgline ${screenErr ? 'warn' : ''}`}>{screenErr || ' '}</p>
              </Card>
            </div>

            {/* ── 오른쪽: 만들어진 검색식과 그 결과 ── */}
            <div className="split-b">
              <Card
                title="검색식"
                sub={screenName || '이름 없음 · 저장 안 됨'}
                right={
                  <>
                    <span className={`badge ${conds.length ? 'on' : ''}`}>조건 {conds.length}</span>
                    <select value={screenName} onChange={(e) => loadScreen(e.target.value)} title="저장된 검색식">
                      <option value="">불러오기…</option>
                      {Object.keys(screens).map((n) => (
                        <option key={n} value={n}>
                          {n}
                        </option>
                      ))}
                    </select>
                  </>
                }
              >
                {conds.length > 0 ? (
                  <ol className="condlist">
                    {conds.map((c, i) => (
                      <li key={`${c.key}-${i}`} className={justAdded === i ? 'flash' : undefined}>
                        <span className="ix">{rowLabel(i)}</span>
                        <span className="tx">{summarizeCond(c, condMap.get(c.key))}</span>
                        <button
                          className="del"
                          title="조건 삭제"
                          onClick={() => setConds((cs) => cs.filter((_, idx) => idx !== i))}
                        >
                          ×
                        </button>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p className="empty-slot">
                    아직 조건이 없습니다.
                    <br />
                    이대로 검색하면 <b>전체 종목</b>이 나옵니다.
                  </p>
                )}

                <div className="form-row" style={{ marginTop: 10 }}>
                  <select
                    style={{ flex: 'none', width: 108 }}
                    value={logic}
                    onChange={(e) => setLogic(e.target.value as 'and' | 'or')}
                  >
                    <option value="and">전체 AND</option>
                    <option value="or">전체 OR</option>
                  </select>
                  <input
                    type="date"
                    style={{ flex: 'none', width: 148 }}
                    value={date}
                    onChange={(e) => setDate(e.target.value)}
                    title="기준일 (빈칸 = 최신 거래일)"
                  />
                  <button className="primary" disabled={running} onClick={() => void run(conds, logic)}>
                    {running ? '조회 중…' : '검색'}
                  </button>
                </div>
                <p className="msgline">{screenMsg || ' '}</p>
              </Card>

              {result && (
                <Card title="검색 결과" flush sub={result.date}>
                <div className="sumcard">
                  <b>{screenName || '이름 없는 검색식'}</b>
                  <div className="pills">
                    <span>
                      검색 <b>{result.total.toLocaleString()}</b>종목
                    </span>
                    <span>
                      조건 <b>{result.conditions ?? conds.length}</b>개
                    </span>
                    <span>
                      수익률(당일){' '}
                      <b className={chgClass(result.avg_chg)}>{fmtChg(result.avg_chg)}</b>
                    </span>
                  </div>
                </div>
                {result.items.length === 0 ? (
                  <p className="hint" style={{ padding: '0 16px' }}>
                    조건에 맞는 종목이 없습니다.
                  </p>
                ) : (
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
                          className={sym?.code === it.code ? 'selected' : undefined}
                          onClick={() => pickSymbol({ code: it.code, name: it.name, market: it.market })}
                        >
                          <td className="nm">
                            {it.name}
                            <small style={{ display: 'block', color: 'var(--hts-text-3)', fontWeight: 400 }}>
                              {it.market} · {it.code}
                            </small>
                          </td>
                          <td className={`num ${chgClass(it.chg)}`}>{fmtPrice(it.close)}</td>
                          <td className={`num ${chgClass(it.chg)}`}>{fmtChg(it.chg)}</td>
                          <td className="num">{fmtEok(it.amount)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                </Card>
              )}
              {runMsg && <p className="hint warn">{runMsg}</p>}
            </div>
          </div>
        )}

        {/* ─────────────── ② 매매전략 ─────────────── */}
        {step === 'strategy' && (
          <>
            <Card
              title="종목선정"
              sub="①에서 만든 검색식 사용"
              right={<span className="badge">{draft.conditions.length}개 조건</span>}
            >
              <div className="form-row">
                <select
                  style={{ flex: 1 }}
                  value={draft.screenName ?? ''}
                  onChange={(e) => attachScreen(e.target.value)}
                >
                  <option value="">검색식 선택… (비우면 전체 종목)</option>
                  {Object.keys(screens).map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
                <button onClick={() => setStep('screen')}>검색식 만들기</button>
              </div>
              {draft.conditions.length === 0 ? (
                <p className="hint">
                  검색식을 고르지 않으면 <b>전체 종목</b>이 대상이 됩니다(제외정책 적용 후).
                </p>
              ) : (
                <table className="grid">
                  <tbody>
                    {draft.conditions.map((c, i) => (
                      <tr key={`${c.key}-${i}`}>
                        <td className="flat" style={{ width: 22 }}>
                          {rowLabel(i)}
                        </td>
                        <td>{summarizeCond(c, condMap.get(c.key))}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              <div className="form-row" style={{ marginTop: 8 }}>
                <button
                  style={{ flex: 1 }}
                  disabled={running}
                  onClick={() => {
                    setStep('screen')
                    void run(draft.conditions, draft.logic)
                  }}
                >
                  이 조건으로 종목 보기
                </button>
              </div>
            </Card>

            <Card title="종목 상세" right={sym && <span className="badge">{sym.code}</span>}>
              <SymbolDetail sym={sym} />
            </Card>

            <Card title="진입 기법" sub="피보나치 등">
              <div className="kv">
                <span className="k">기법</span>
                <span className="v">
                  <select
                    style={{ flex: 1 }}
                    value={draft.entryKey}
                    onChange={(e) => {
                      set('entryKey', e.target.value)
                      set('entryParams', {})
                      setEntryErr('')
                    }}
                  >
                    <option value="">선택…</option>
                    {stratCat.map((s) => (
                      <option key={s.key} value={s.key}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                </span>
              </div>
              {entryDef ? (
                <>
                  <p className="hint">{entryDef.desc}</p>
                  <ParamInputs
                    defs={entryDef.params}
                    values={draft.entryParams}
                    onChange={(k, v) => set('entryParams', { ...draft.entryParams, [k]: v })}
                  />
                  {(entryDef.overlay || entryDef.signals) && (
                    <div className="form-row" style={{ marginTop: 8 }}>
                      <button style={{ flex: 1 }} onClick={showOnChart}>
                        차트에 표시
                      </button>
                      <button className="ghost" onClick={() => pickStrategy(null)}>
                        해제
                      </button>
                    </div>
                  )}
                </>
              ) : (
                <p className="hint">기법을 선택하면 파라미터 입력 폼이 나옵니다.</p>
              )}
              {entryErr && <p className="hint warn">{entryErr}</p>}
            </Card>

            <Card title="매수 주문조건">
              <div className="kv">
                <span className="k">주문 구분</span>
                <span className="v">
                  <span className="radios" style={{ marginLeft: 'auto' }}>
                    <label>
                      <input
                        type="radio"
                        checked={draft.priceType === 'limit'}
                        onChange={() => set('priceType', 'limit')}
                      />
                      보통가(지정가)
                    </label>
                    <label>
                      <input
                        type="radio"
                        checked={draft.priceType === 'market'}
                        onChange={() => set('priceType', 'market')}
                      />
                      시장가
                    </label>
                  </span>
                </span>
              </div>
              <div className="kv">
                <span className="k">신용 구분</span>
                <span className="v">
                  <span className="radios" style={{ marginLeft: 'auto' }}>
                    {(['cash', 'credit'] as CreditType[]).map((c) => (
                      <label key={c}>
                        <input type="radio" checked={draft.credit === c} onChange={() => set('credit', c)} />
                        {c === 'cash' ? '현금' : '신용'}
                      </label>
                    ))}
                  </span>
                </span>
              </div>
              <div className="kv">
                <span className="k">주문수량</span>
                <span className="v">
                  <select
                    style={{ flex: 'none', width: 84 }}
                    value={draft.qtyType}
                    onChange={(e) => set('qtyType', e.target.value as 'shares' | 'amount')}
                  >
                    <option value="shares">수량</option>
                    <option value="amount">금액</option>
                  </select>
                  <input className="amt" value={draft.qty} onChange={(e) => set('qty', e.target.value)} />
                  <span className="unit">{draft.qtyType === 'shares' ? '주' : '원'}</span>
                </span>
              </div>
              <p className="hint">주문은 나가지 않습니다 — 전략 정의 저장용.</p>
            </Card>

            {msg && <p className="hint">{msg}</p>}
          </>
        )}

      </div>

      {/* 단계별 하단 액션 */}
      {step === 'screen' && (
        <div className="actionbar">
          <button onClick={newScreen}>새 검색식</button>
          {namingScreen ? (
            <>
              <input
                autoFocus
                placeholder="검색식 이름"
                value={screenNameDraft}
                onChange={(e) => setScreenNameDraft(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && doSaveScreen()}
              />
              <button className="cta" onClick={doSaveScreen}>
                확인
              </button>
            </>
          ) : (
            <>
              <button
                disabled={!screenName}
                onClick={() => {
                  setScreens(deleteScreen(screens, screenName))
                  newScreen()
                  setScreenMsg(`검색식 [${screenName}] 삭제됨`)
                }}
              >
                삭제
              </button>
              <button
                className="cta"
                onClick={() => {
                  setScreenNameDraft(screenName)
                  setNamingScreen(true)
                }}
              >
                검색식 저장
              </button>
            </>
          )}
        </div>
      )}

      {step === 'strategy' && (
        <div className="actionbar">
          <select value={name} onChange={(e) => loadSaved(e.target.value)} title="저장된 전략">
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
                autoFocus
                placeholder="전략 이름"
                value={nameDraft}
                onChange={(e) => setNameDraft(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && doSave()}
              />
              <button className="cta" onClick={doSave}>
                확인
              </button>
            </>
          ) : confirmDel ? (
            <>
              <button onClick={doDelete}>정말 삭제</button>
              <button onClick={() => setConfirmDel(false)}>취소</button>
            </>
          ) : (
            <>
              <button onClick={newStrategy}>새 전략</button>
              <button disabled={!name} onClick={() => setConfirmDel(true)}>
                삭제
              </button>
              <button className="cta" onClick={beginSave}>
                전략 저장
              </button>
            </>
          )}
        </div>
      )}
    </div>
  )
}
