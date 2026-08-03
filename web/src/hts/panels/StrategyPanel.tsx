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
  type TargetKind,
} from './strategyStore'
import {
  fmtPeriod,
  loadWatches,
  newId,
  periodFrom,
  saveWatches,
  type WatchOrder,
  type WatchSide,
} from './watchStore'

// 전략 화면은 3단계다.
//  ① 종목선정 — 조건검색식을 여러 개 만들고 고친다 (저장소: hts-screens)
//  ② 매매전략 — 그중 하나를 골라 진입기법·목표가·주문조건을 붙인다 (저장소: hts-strategies)
//  ③ 감시    — ②를 특정 종목에 걸어둔 시세포착 감시 목록 (저장소: hts-watchorders)
// 정량 값은 전부 이 화면에서 입력한다 (ADR-0009 — 전략 숫자 하드코딩 금지).

const LIMIT = 100
const DAY_PRESETS = [1, 7, 15, 30]

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

const TARGET_LABEL: Record<TargetKind, string> = {
  fib: '피보나치 레벨',
  round: '라운드 피겨',
  manual: '직접 입력',
}

type Step = 'screen' | 'strategy' | 'watch'

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

  const condDef = condMap.get(condKey)
  const catConds = condCats.find((c) => c.key === catKey)?.conditions ?? []

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
    setConds((cs) => [...cs, { key: condDef.key, params: r.value }])
    setCondDraft({})
    setScreenErr('')
  }

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

  // ── ③ 감시 ──
  const [watches, setWatches] = useState<WatchOrder[]>(loadWatches)
  const [tab, setTab] = useState<WatchSide | 'log'>('buy')
  const [watchMsg, setWatchMsg] = useState('')

  function putWatches(next: WatchOrder[]) {
    setWatches(saveWatches(next))
  }

  function addWatch(side: WatchSide) {
    if (!sym) {
      setWatchMsg('감시를 걸 종목을 먼저 고르세요.')
      return
    }
    const target = Number(draft.manualPrice)
    if (!Number.isFinite(target) || target <= 0) {
      setWatchMsg('② 매매전략의 목표가를 먼저 입력하세요.')
      return
    }
    const qty = Number(draft.qty)
    if (!Number.isFinite(qty) || qty <= 0) {
      setWatchMsg('② 매매전략의 주문수량을 먼저 입력하세요.')
      return
    }
    const { from, to } = periodFrom(Number(draft.validDays) || 7)
    putWatches([
      {
        id: newId(),
        side,
        code: sym.code,
        name: sym.name,
        target,
        qty,
        qtyType: draft.qtyType,
        priceType: draft.priceType,
        tick: Number(draft.tick) || 0,
        credit: draft.credit,
        from,
        to,
        state: 'run',
        ...(name ? { strategy: name } : {}),
      },
      ...watches,
    ])
    setTab(side)
    setStep('watch')
    setWatchMsg(`[${sym.name}] ${side === 'buy' ? '매수' : '매도'} 감시 등록 — 주문은 나가지 않습니다.`)
  }

  const shownWatches = watches.filter((w) => tab !== 'log' && w.side === tab)

  return (
    <div className="panel-col">
      <div className="steps">
        {(
          [
            ['screen', '① 종목선정', `검색식 ${Object.keys(screens).length}`],
            ['strategy', '② 매매전략', `전략 ${Object.keys(saved).length}`],
            ['watch', '③ 감시', `${watches.length}건`],
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
          <>
            <Card
              title="조건검색식"
              sub="여러 개 만들어 두고 ②에서 골라 쓴다"
              right={
                <select value={screenName} onChange={(e) => loadScreen(e.target.value)} title="저장된 검색식">
                  <option value="">검색식 선택…</option>
                  {Object.keys(screens).map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              }
            >
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
                  style={{ flex: 1 }}
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
                  style={{ flex: 1 }}
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

              {condDef && (
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
                  <div className="form-row" style={{ marginTop: 8 }}>
                    <button className="primary" style={{ flex: 1 }} onClick={addCond}>
                      조건 추가
                    </button>
                  </div>
                </>
              )}
              {screenErr && <p className="hint warn">{screenErr}</p>}

              {conds.length > 0 ? (
                <table className="grid">
                  <tbody>
                    {conds.map((c, i) => (
                      <tr key={`${c.key}-${i}`}>
                        <td className="flat" style={{ width: 22 }}>
                          {rowLabel(i)}
                        </td>
                        <td>{summarizeCond(c, condMap.get(c.key))}</td>
                        <td className="num" style={{ width: 40 }}>
                          <button
                            className="row-del"
                            title="조건 삭제"
                            onClick={() => setConds((cs) => cs.filter((_, idx) => idx !== i))}
                          >
                            ×
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p className="hint">조건이 없습니다 — 이대로 검색하면 <b>전체 종목</b>이 됩니다.</p>
              )}

              <div className="form-row" style={{ marginTop: 10 }}>
                <select value={logic} onChange={(e) => setLogic(e.target.value as 'and' | 'or')}>
                  <option value="and">전체 AND</option>
                  <option value="or">전체 OR</option>
                </select>
                <input
                  type="date"
                  style={{ flex: 'none', width: 140 }}
                  value={date}
                  onChange={(e) => setDate(e.target.value)}
                  title="기준일 (빈칸 = 최신 거래일)"
                />
                <button className="primary" style={{ flex: 1 }} disabled={running} onClick={() => void run(conds, logic)}>
                  {running ? '조회 중…' : '검색'}
                </button>
              </div>
              {screenMsg && <p className="hint">{screenMsg}</p>}
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
          </>
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

            <Card title="시세포착" sub="목표가 감시조건">
              <div className="kv">
                <span className="k">목표가</span>
                <span className="v">
                  <input
                    className="amt"
                    placeholder="192100"
                    value={draft.manualPrice}
                    onChange={(e) => set('manualPrice', e.target.value)}
                  />
                  <span className="unit">원</span>
                </span>
              </div>
              <div className="kv">
                <span className="k">목표가 기준</span>
                <span className="v">
                  <select
                    style={{ flex: 1 }}
                    value={draft.target}
                    onChange={(e) => set('target', e.target.value as TargetKind)}
                  >
                    {(Object.keys(TARGET_LABEL) as TargetKind[]).map((t) => (
                      <option key={t} value={t}>
                        {TARGET_LABEL[t]}
                      </option>
                    ))}
                  </select>
                </span>
              </div>
              <div className="kv">
                <span className="k">근접 허용</span>
                <span className="v">
                  <input
                    className="amt"
                    placeholder={PLACEHOLDER.near}
                    value={draft.near}
                    onChange={(e) => set('near', e.target.value)}
                  />
                  <span className="unit">%</span>
                </span>
              </div>
              <p className="hint">※ 목표가가 현재가보다 높으면 추격매수로 실행됩니다. 판단·계산은 전부 서버가 합니다.</p>
              <div className="form-row" style={{ marginTop: 8 }}>
                <button style={{ flex: 1 }} onClick={() => addWatch('buy')}>
                  이 종목 매수감시 등록
                </button>
                <button style={{ flex: 1 }} onClick={() => addWatch('sell')}>
                  매도감시
                </button>
              </div>
              {watchMsg && <p className="hint">{watchMsg}</p>}
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
                <span className="k">도달가격의</span>
                <span className="v">
                  <input className="amt" value={draft.tick} onChange={(e) => set('tick', e.target.value)} />
                  <span className="unit">틱</span>
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
              <div className="kv">
                <span className="k">설정기간</span>
                <span className="v">
                  <span className="badge">{fmtPeriod(periodFrom(Number(draft.validDays) || 0))}</span>
                </span>
              </div>
              <div className="chips" style={{ marginTop: 4 }}>
                {DAY_PRESETS.map((d) => (
                  <button
                    key={d}
                    className={`chip ${draft.validDays === String(d) ? 'on' : ''}`}
                    onClick={() => set('validDays', String(d))}
                  >
                    {d}일
                  </button>
                ))}
                <input
                  className="amt"
                  style={{ width: 72 }}
                  placeholder="직접"
                  value={draft.validDays}
                  onChange={(e) => set('validDays', e.target.value)}
                />
              </div>
              <p className="hint">최대 30일. 주문은 나가지 않습니다 — 전략 정의 저장용.</p>
            </Card>

            {msg && <p className="hint">{msg}</p>}
          </>
        )}

        {/* ─────────────── ③ 감시 ─────────────── */}
        {step === 'watch' && (
          <section className="card">
            <div className="hd">
              시세포착 감시
              <span className="sub">목표가 도달 시 실행할 감시</span>
              <span className="right">
                <span className="badge on">{shownWatches.length}건</span>
              </span>
            </div>
            <div className="tabs">
              <button className={tab === 'buy' ? 'on' : ''} onClick={() => setTab('buy')}>
                매수감시
              </button>
              <button className={tab === 'sell' ? 'on' : ''} onClick={() => setTab('sell')}>
                매도감시
              </button>
              <button className={tab === 'log' ? 'on' : ''} onClick={() => setTab('log')}>
                주문내역
              </button>
            </div>
            <div className="bd flush">
              {tab === 'log' ? (
                <p className="hint" style={{ padding: '14px 16px' }}>
                  주문내역은 KIS 모의투자 연동(단계 5) 후 표시됩니다. 지금은 감시 정의만 저장합니다.
                </p>
              ) : shownWatches.length === 0 ? (
                <p className="hint" style={{ padding: '14px 16px' }}>
                  등록된 감시가 없습니다. ② 매매전략에서 목표가·수량을 채운 뒤 등록하세요.
                </p>
              ) : (
                shownWatches.map((w) => (
                  <div
                    key={w.id}
                    className={`listrow ${w.side}`}
                    onClick={() => pickSymbol({ code: w.code, name: w.name, market: '' })}
                  >
                    <div className="r1">
                      <span className="nm">{w.name}</span>
                      <span className="badge">{w.code}</span>
                      {w.strategy && <span className="badge on">{w.strategy}</span>}
                      <span className="grow" />
                      <button
                        className="ghost"
                        title="감시 삭제"
                        onClick={(e) => {
                          e.stopPropagation()
                          putWatches(watches.filter((x) => x.id !== w.id))
                        }}
                      >
                        ×
                      </button>
                    </div>
                    <div className="r2">
                      <span>{w.credit === 'cash' ? '현금' : '신용'}</span>
                      <b style={{ color: 'var(--hts-text)' }}>
                        {w.qty.toLocaleString()}
                        {w.qtyType === 'shares' ? '주' : '원'}
                      </b>
                      <span className="grow" />
                      <span>{fmtPeriod(w)}</span>
                    </div>
                    <div className="r3">
                      <span className="badge">가격기준</span>
                      <span>목표가</span>
                      <b style={{ color: 'var(--hts-text)' }}>{fmtPrice(w.target)}원</b>
                      <span className="grow" />
                      <span>{w.priceType === 'market' ? '시장가' : '지정가'}</span>
                      <span>{w.tick}틱</span>
                    </div>
                    <div className="r3">
                      <button
                        className={`ghost state ${w.state === 'run' ? 'run' : 'hold'}`}
                        onClick={(e) => {
                          e.stopPropagation()
                          putWatches(
                            watches.map((x) =>
                              x.id === w.id ? { ...x, state: x.state === 'run' ? 'hold' : 'run' } : x,
                            ),
                          )
                        }}
                      >
                        {w.state === 'run' ? '▶ 감시' : '❚❚ 중지'}
                      </button>
                    </div>
                  </div>
                ))
              )}
              {watchMsg && (
                <p className="hint" style={{ padding: '4px 16px 0' }}>
                  {watchMsg}
                </p>
              )}
            </div>
          </section>
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
