import { useEffect, useMemo, useRef, useState } from 'react'
import {
  fetchConditions,
  fetchQuotes,
  fetchStrategies,
  postSimulate,
  runScreen,
  type ConditionCategory,
  type ConditionDef,
  type ConditionParamDef,
  type Quote,
  type ScreenResponse,
  type SimulateResponse,
  type StrategyDef,
} from '../../api'
import { ProChart, type ProChartHandle } from '../../ProChart'
import { currentSymbol, onSymbolPick, pickSymbol, type SymbolPick } from '../bus'
import { chgClass, fmtChg, fmtEok, fmtPrice } from '../format'
import { MiniCandles } from '../MiniCandles'
import { BuyStages, SellStages, type ComputedPrices } from './SplitStages'
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
  newBuyStage,
  parseParams,
  saveStrategy,
  toDraft,
  toStrategy,
  type SavedCondition,
  type Strategies,
  type StrategyDraft,
} from './strategyStore'

// 전략 화면은 3단계다.
//  ① 종목선정 — 조건검색식을 여러 개 만들고 고친다 (저장소: hts-screens)
//  ② 매매전략 — 그중 하나를 골라 분할 매수/매도·주문조건을 붙인다 (저장소: hts-strategies)
//  ③ 시뮬레이션 — 고른 종목에 전략 1호(급등 앵커+분할)를 돌려 전용 차트로 확인 (오너 지시:
//    종목 차트 오버레이 ❌, 이 탭에서 본다)
// 정량 값은 전부 이 화면에서 입력한다 (ADR-0009 — 전략 숫자 하드코딩 금지).

const LIMIT = 100

// ③ 시뮬레이션 대표 종목 — 오너 지시(2026-08-05)로 고정. 전략 설계 확인용 기준 종목이다.
const SIM_SYM = { code: '005930', name: '삼성전자', market: 'KOSPI' } as const

// ③ 예시 기본값 — 지위는 PLACEHOLDER 와 같다 (ADR-0009: 서버 하드코딩 금지, UI 예시는 허용).
// 실행 시 **빈 항목만** 이 값으로 채우고, 채운 값은 전부 화면(분할 카드·메시지)에 보인다.
// "실행 버튼을 누르면 무조건 예시가 보여야 한다"(오너) — 빈 폼 때문에 실행을 막지 않는다.
const SIM_EXAMPLE = {
  window: 20,
  gainPct: 30,
  tolerancePct: 1.5,
  qtyShares: 100,
  buy: [
    { ratio: 0.382, weight: 33 },
    { ratio: 0.5, weight: 33 },
    { ratio: 0.618, weight: 34 },
  ],
} as const

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

type Step = 'screen' | 'strategy' | 'sim'

function rowLabel(i: number): string {
  return i < 26 ? String.fromCharCode(65 + i) : String(i + 1)
}

function fmtVal(v: number, unit: string): string {
  return `${v.toLocaleString()}${unit}`
}

function fmtMarket(m: string): string {
  if (m.startsWith('KOSPI')) return '코스피'
  if (m.startsWith('KOSDAQ')) return '코스닥'
  return m
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

// ③ 차트 하단 결과 스트립 — 앵커 근거·지지선·최종 손익을 한 줄씩. 차트를 보면서 같이 읽는 용도.
function SimFoot({ r }: { r: SimulateResponse }) {
  const avwapLast = r.series.find((s) => s.label.includes('VWAP'))?.points.at(-1)?.value
  const stopLine = r.lines.find((l) => l.kind === 'stop')
  const buys = r.fills.filter((f) => f.side === 'buy').length
  const sells = r.fills.filter((f) => f.side === 'sell').length
  const t = r.trades
  const total = t ? t.realized_pnl + t.unrealized_pnl : null
  return (
    <div className="sim-foot">
      <p>
        <b>급등 파동</b> {r.anchor.start_date} {fmtPrice(r.anchor.start_price)} →{' '}
        {r.anchor.end_date} {fmtPrice(r.anchor.end_price)} (+{r.anchor.gain_pct.toFixed(1)}%)
        {r.anchor.is_52w_high ? ' · 52주 신고가' : ' · 52주 신고가 아님'}
      </p>
      <p>
        <b>지지선 근거</b> 급등 시작가 {fmtPrice(r.anchor.start_price)}
        {avwapLast != null && <> · 앵커 VWAP {fmtPrice(Math.round(avwapLast))} (급등에 올라탄 사람들의 평균 매수가)</>}
        {stopLine && <> · 손절선 {fmtPrice(stopLine.price)}</>}
      </p>
      {t && total != null ? (
        <p>
          <b>결과</b> 매수 {t.buys.length}건 체결
          {t.avg_entry != null && <> → 평단 {fmtPrice(t.avg_entry)}</>} · 매도 {t.sells.length}건 →
          실현 <b className={chgClass(t.realized_pnl)}>{Math.round(t.realized_pnl).toLocaleString()}원</b>
          {t.remain_shares > 0 && (
            <>
              {' '}· 잔여 {t.remain_shares.toLocaleString()}주 평가{' '}
              <b className={chgClass(t.unrealized_pnl)}>{Math.round(t.unrealized_pnl).toLocaleString()}원</b>
            </>
          )}{' '}
          = 합계 <b className={chgClass(total)}>{total > 0 ? '+' : ''}{Math.round(total).toLocaleString()}원</b>
          <span className="dim"> (수수료·세금·슬리피지 미포함)</span>
        </p>
      ) : (
        <p>
          <b>결과</b> 체결 지점 매수 {buys} · 매도 {sells} — 모의 수량을 넣으면 손익까지 계산됩니다.
        </p>
      )}
    </div>
  )
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
    const load = () =>
      void fetchQuotes([code], true).then((r) => {
        if (id === req.current) setQ(r.quotes[0] ?? null)
      })
    load()
    const t = window.setInterval(load, 10_000) // 실시간 시세 폴링
    return () => window.clearInterval(t)
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
        <MiniCandles data={q?.candles} width={520} height={64} />
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

  // ── ③ 시뮬레이션 — 전략 1호(급등 앵커 + 분할). 계산은 전부 파이썬(/api/simulate) ──
  //
  // 종목은 **대표 종목(삼성전자) 고정**이다 — ① 에서 뭘 골랐는지와 무관 (오너 지시 2026-08-05:
  // "내가 설계한 전략이 어떻게 되는지만 보고 싶은 거"). 이 화면은 전략 설계를 눈으로
  // 확인하는 자리지 종목 검증 자리가 아니다. 실전 적용은 백테스트 러너(ADR-0007) 몫.
  //
  // 입력은 **기준일 하나**다 (오너 지시). 급등 기준은 전략에 담긴 ① 검색식 조건에서 읽고,
  // 나머지 빈 값은 예시값으로 채워서 실행이 절대 막히지 않게 한다 — 채운 값은 화면에 보인다.
  const proRef = useRef<ProChartHandle>(null)
  const [simDate, setSimDate] = useState('')
  const [simMsg, setSimMsg] = useState('')
  const [simRunning, setSimRunning] = useState(false)
  const [simResult, setSimResult] = useState<SimulateResponse | null>(null)
  const [computed, setComputed] = useState<ComputedPrices>({})

  // 급등 기준 — ① 조건검색식 소관이다 (오너: "급등 퍼센테이지는 조건 검색식이지").
  // 전략이 담고 있는 검색식 조건에서 읽는다. 없으면 예시값(화면에 예시임을 명시).
  const surge = useMemo(() => {
    const cum = draft.conditions.find(
      (c) => c.key === 'cum_change' && c.params['days'] > 0 && c.params['min'] > 0,
    )
    if (cum) {
      return {
        window: cum.params['days'],
        gainPct: cum.params['min'],
        src: `검색식 조건에서 — ${cum.params['days']}일 누적등락률 +${cum.params['min']}% 이상`,
      }
    }
    const day = draft.conditions.find((c) => c.key === 'change_range' && c.params['min'] > 0)
    if (day) {
      return { window: 1, gainPct: day.params['min'], src: `검색식 조건에서 — 당일등락률 +${day.params['min']}% 이상` }
    }
    return { window: SIM_EXAMPLE.window, gainPct: SIM_EXAMPLE.gainPct, src: '' }
  }, [draft.conditions])

  // 최신 결과를 ref 로도 들고 있는다 — 탭 재진입 효과가 simResult 를 deps 에 넣으면
  // 실행할 때마다 showSymbol(데이터 재로드)이 돌아 줌이 풀리기 때문이다.
  const simResultRef = useRef<SimulateResponse | null>(null)
  useEffect(() => {
    simResultRef.current = simResult
  }, [simResult])

  // ③ 에 들어오면(차트가 새로 마운트되면) 대표 종목을 싣고, 직전 결과가 있으면 다시 그린다.
  useEffect(() => {
    if (step !== 'sim') return
    proRef.current?.showSymbol(SIM_SYM.code, SIM_SYM.name, SIM_SYM.market)
    const r = simResultRef.current
    if (r) proRef.current?.applySimulation({ lines: r.lines, fills: r.fills, series: r.series })
  }, [step])

  async function runSimulation() {
    // 빈 값 때문에 실행을 막지 않는다 — 예시값으로 채우고, 채운 사실을 메시지로 알린다.
    const filled: string[] = []

    let buy = draft.buy
    if (!buy.some((b) => b.enabled && b.ratio > 0 && b.ratio < 1 && b.weight > 0)) {
      buy = SIM_EXAMPLE.buy.map((b) => newBuyStage(b.ratio, b.weight))
      set('buy', buy)
      filled.push('분할 매수 3차(38.2/50/61.8%)')
    }

    let tol = Number(draft.roundTolerancePct)
    if (!(tol > 0)) {
      tol = SIM_EXAMPLE.tolerancePct
      set('roundTolerancePct', String(tol))
      filled.push(`라운드 허용폭 ${tol}%`)
    }

    const hasQty = Number(draft.qty) > 0
    const qty = hasQty ? Number(draft.qty) : SIM_EXAMPLE.qtyShares
    if (!hasQty) filled.push(`수량 ${SIM_EXAMPLE.qtyShares}주`)

    setSimRunning(true)
    setSimMsg('계산 중…')
    try {
      const res = await postSimulate({
        code: SIM_SYM.code,
        end: simDate || undefined,
        window: surge.window,
        min_gain_pct: surge.gainPct,
        buy: buy.map((b) => ({
          id: b.id, ratio: b.ratio, weight: b.weight, enabled: b.enabled, price_override: b.priceOverride,
        })),
        sell: draft.sell.map((s) => ({
          id: s.id, rebound_pct: s.reboundPct, weight: s.weight, enabled: s.enabled, price_override: s.priceOverride,
        })),
        sell_basis: draft.sellBasis,
        round_tolerance_pct: tol,
        qty,
        qty_type: hasQty ? draft.qtyType : 'shares',
        stop: draft.stopEnabled
          ? {
              enabled: true,
              mode: draft.stopMode,
              pct: Number(draft.stopPct) > 0 ? Number(draft.stopPct) : undefined,
              source: draft.stopSource,
              custom_price: Number(draft.stopCustom) > 0 ? Number(draft.stopCustom) : undefined,
              tick_offset: Number.isInteger(Number(draft.stopTicks)) ? Number(draft.stopTicks) : 0,
            }
          : undefined,
      })
      setSimResult(res)
      setComputed(res.computed)
      proRef.current?.applySimulation({ lines: res.lines, fills: res.fills, series: res.series })
      setSimMsg(filled.length ? `예시값 사용: ${filled.join(' · ')}` : '')
    } catch (e) {
      setSimResult(null)
      proRef.current?.applySimulation(null)
      setSimMsg(e instanceof Error ? e.message : '시뮬레이션 실패')
    } finally {
      setSimRunning(false)
    }
  }

  return (
    <div className="panel-col">
      <div className="steps">
        {(
          [
            ['screen', '① 종목선정', `검색식 ${Object.keys(screens).length}`],
            ['strategy', '② 매매전략', `전략 ${Object.keys(saved).length}`],
            ['sim', '③ 시뮬레이션', `${SIM_SYM.name} 기준`],
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
                        {condKey === 'new_high' && ' · 52주 ≈ 250거래일 (기간+이내 ≤ 520)'}
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

              <Card title="종목 상세" right={sym && <span className="badge">{sym.code}</span>}>
                <SymbolDetail sym={sym} />
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
                  <>
                    {result.themes_ready === false && (
                      <p className="hint" style={{ padding: '0 16px' }}>
                        테마 수집 중 — 잠시 후 다시 검색하면 표시됩니다.
                      </p>
                    )}
                    <ul className="hitlist">
                      {result.items.map((it) => (
                        <li
                          key={it.code}
                          className={sym?.code === it.code ? 'selected' : undefined}
                          onClick={() => pickSymbol({ code: it.code, name: it.name, market: it.market })}
                        >
                          <MiniCandles data={it.candles} />
                          <div className="who">
                            <span className="nm">{it.name}</span>
                            <small>
                              <span className={`mkt ${it.market.startsWith('KOSPI') ? 'kospi' : 'kosdaq'}`}>
                                {fmtMarket(it.market)}
                              </span>{' '}
                              {it.code}
                            </small>
                          </div>
                          <div className={`px num ${chgClass(it.chg)}`}>
                            <span className="close">{fmtPrice(it.close)}</span>
                            <small>{fmtChg(it.chg)}</small>
                          </div>
                          <div className="etc num">
                            <small>거래대금 {fmtEok(it.amount)}</small>
                            {it.themes && it.themes.length > 0 && (
                              <span className="themes" title={it.themes.join(' · ')}>
                                {it.themes.slice(0, 2).join(' · ')}
                                {it.themes.length > 2 && ` 외 ${it.themes.length - 2}`}
                              </span>
                            )}
                          </div>
                        </li>
                      ))}
                    </ul>
                  </>
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
              <p className="hint">종목 확인은 ① 종목선정 탭에서 — 검색식을 고르고 검색하세요.</p>
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
                </>
              ) : (
                <p className="hint">기법을 선택하면 파라미터 입력 폼이 나옵니다.</p>
              )}
              {entryErr && <p className="hint warn">{entryErr}</p>}
            </Card>

            <Card title="분할 매수" sub="되돌림 레벨의 라운드 피겨에 건다 (ADR-0011)">
              <BuyStages stages={draft.buy} computed={computed} onChange={(b) => set('buy', b)} />
            </Card>

            <Card title="분할 매도" sub="기준점 대비 반등률">
              <div className="kv">
                <span className="k">매도 기준점</span>
                <span className="v">
                  <span className="radios" style={{ marginLeft: 'auto' }}>
                    {(
                      [
                        ['avg_entry', '매수 평단'],
                        ['lowest_fill', '최저 체결가'],
                        ['anchor_high', '앵커 고점'],
                      ] as const
                    ).map(([k, label]) => (
                      <label key={k}>
                        <input type="radio" checked={draft.sellBasis === k} onChange={() => set('sellBasis', k)} />
                        {label}
                      </label>
                    ))}
                  </span>
                </span>
              </div>
              <SellStages stages={draft.sell} computed={computed} onChange={(s) => set('sell', s)} />
              <div className="kv" style={{ marginTop: 8 }}>
                <span className="k">라운드 허용폭</span>
                <span className="v">
                  <input
                    className="amt"
                    placeholder="1.5"
                    value={draft.roundTolerancePct}
                    onChange={(e) => set('roundTolerancePct', e.target.value)}
                  />
                  <span className="unit">%</span>
                </span>
              </div>
            </Card>

            <Card title="손절" sub="평단 -% 또는 지지저항 ±N호가">
              <div className="kv">
                <span className="k">사용</span>
                <span className="v">
                  <span className="radios" style={{ marginLeft: 'auto' }}>
                    <label>
                      <input type="checkbox" checked={draft.stopEnabled} onChange={(e) => set('stopEnabled', e.target.checked)} />
                      손절 건다
                    </label>
                  </span>
                </span>
              </div>
              {draft.stopEnabled && (
                <>
                  <div className="kv">
                    <span className="k">방식</span>
                    <span className="v">
                      <span className="radios" style={{ marginLeft: 'auto' }}>
                        <label>
                          <input type="radio" checked={draft.stopMode === 'pct'} onChange={() => set('stopMode', 'pct')} />
                          평단 대비 %
                        </label>
                        <label>
                          <input type="radio" checked={draft.stopMode === 'support'} onChange={() => set('stopMode', 'support')} />
                          지지저항 기준
                        </label>
                      </span>
                    </span>
                  </div>
                  {draft.stopMode === 'pct' ? (
                    <div className="kv">
                      <span className="k">평단에서</span>
                      <span className="v">
                        <input className="amt" placeholder="3" value={draft.stopPct} onChange={(e) => set('stopPct', e.target.value)} />
                        <span className="unit">% 아래</span>
                      </span>
                    </div>
                  ) : (
                    <>
                      <div className="kv">
                        <span className="k">기준선</span>
                        <span className="v">
                          <select
                            style={{ flex: 1 }}
                            value={draft.stopSource}
                            onChange={(e) => set('stopSource', e.target.value as 'avwap' | 'anchor_start' | 'custom')}
                          >
                            <option value="avwap">앵커 VWAP (지지선)</option>
                            <option value="anchor_start">급등 시작가</option>
                            <option value="custom">직접 가격</option>
                          </select>
                        </span>
                      </div>
                      {draft.stopSource === 'custom' && (
                        <div className="kv">
                          <span className="k">기준 가격</span>
                          <span className="v">
                            <input className="amt" value={draft.stopCustom} onChange={(e) => set('stopCustom', e.target.value)} />
                            <span className="unit">원</span>
                          </span>
                        </div>
                      )}
                      <div className="kv">
                        <span className="k">기준선에서</span>
                        <span className="v">
                          <input
                            className="amt"
                            placeholder="-2"
                            value={draft.stopTicks}
                            onChange={(e) => set('stopTicks', e.target.value)}
                          />
                          <span className="unit">호가</span>
                        </span>
                      </div>
                      <p className="hint">
                        기준선에서 몇 호가 아래(−)/위(+)에 걸지. 예: -2 = 2호가 아래.
                        호가 = 그 가격대의 최소 단위(2천원대 1원, 60만원대 1,000원)라 어느
                        가격대든 "두 칸 아래"로 뜻이 같습니다.
                      </p>
                    </>
                  )}
                </>
              )}
              <p className="hint">목표가·손절선·체결 마커는 ③ 시뮬레이션 탭에서 본다.</p>
            </Card>

            {/* "매수 주문조건" 카드는 삭제했다 (오너 지적 2026-08-05).
                — 지정가/시장가: 이 전략은 미리 걸어둔 지정가로 받는 방식이라 선택지 자체가 없다.
                — 주문수량: 분할 차수의 비중(%)과 역할이 겹쳤다. 손익 계산용 수량은 ③으로 이동.
                — 신용 구분: 주문 전송이 없는 지금 단계(CLAUDE.md 단계 6 이전)에는 무의미.
                실주문 조건은 모의투자 주문을 붙이는 새 ADR 때 다시 만든다. */}

            {msg && <p className="hint">{msg}</p>}
          </>
        )}

        {/* ─────────────── ③ 시뮬레이션 ─────────────── */}
        {step === 'sim' && (
          <div className="sim-split">
            <div className="sim-side">
              <Card title="시뮬레이션" sub={`${SIM_SYM.name} 고정 — 전략 1호`}>
                {/* 입력은 전략 선택 + 기준일뿐이다 (오너: "기준일만 넣어").
                    급등 기준은 전략의 ① 검색식 조건에서 읽고, 빈 값은 예시로 채워 실행한다. */}
                <div className="kv">
                  <span className="k">전략</span>
                  <span className="v">
                    <select
                      style={{ flex: 1 }}
                      value={saved[name] ? name : ''}
                      onChange={(e) => {
                        const n = e.target.value
                        if (n && saved[n]) {
                          setName(n)
                          setDraft(toDraft(saved[n]))
                        }
                      }}
                    >
                      <option value="">지금 편집 중인 값 {Object.keys(saved).length === 0 ? '(저장된 전략 없음)' : ''}</option>
                      {Object.keys(saved).map((n) => (
                        <option key={n} value={n}>{n}</option>
                      ))}
                    </select>
                  </span>
                </div>
                <div className="kv">
                  <span className="k">기준일</span>
                  <span className="v">
                    <input
                      type="date"
                      value={simDate}
                      onChange={(e) => setSimDate(e.target.value)}
                      title="빈칸 = 최신 거래일. 과거 날짜를 주면 그 시점을 재현한다."
                    />
                  </span>
                </div>
                <div className="kv">
                  <span className="k">급등 기준</span>
                  <span className="v">최근 {surge.window}거래일 +{surge.gainPct}%</span>
                </div>
                <p className="hint">
                  {surge.src ||
                    '검색식에 등락률 조건이 없어 예시값입니다. 급등 기준(며칠·몇 %)은 ① 조건검색식에서 정합니다.'}
                </p>
                <div className="form-row" style={{ marginTop: 8 }}>
                  <button className="primary" style={{ flex: 1 }} disabled={simRunning} onClick={() => void runSimulation()}>
                    {simRunning ? '계산 중…' : '시뮬레이션 실행'}
                  </button>
                  <button className="ghost" onClick={() => { proRef.current?.applySimulation(null); setSimResult(null); setSimMsg('') }}>
                    지우기
                  </button>
                </div>
                <p className={`msgline ${simMsg ? 'warn' : ''}`}>{simMsg || ' '}</p>
                {simResult && (
                  <table className="grid">
                    <tbody>
                      <tr>
                        <td className="flat">급등 파동</td>
                        <td className="num">
                          {simResult.anchor.start_date} → {simResult.anchor.end_date} (+
                          {simResult.anchor.gain_pct.toFixed(1)}%)
                        </td>
                      </tr>
                      <tr>
                        <td className="flat">앵커</td>
                        <td className="num">
                          {fmtPrice(simResult.anchor.start_price)} ~ {fmtPrice(simResult.anchor.end_price)}
                        </td>
                      </tr>
                      <tr>
                        <td className="flat">52주 신고가</td>
                        <td className="num">{simResult.anchor.is_52w_high ? '예' : '아니오 — 파동 고점이 52주 최고가 아님'}</td>
                      </tr>
                      <tr>
                        <td className="flat">체결 마커</td>
                        <td className="num">
                          매수 {simResult.fills.filter((f) => f.side === 'buy').length} · 매도{' '}
                          {simResult.fills.filter((f) => f.side === 'sell').length}
                        </td>
                      </tr>
                    </tbody>
                  </table>
                )}
              </Card>

              {simResult && (
                <Card title="체결 내역" sub={simResult.trades ? undefined : '②에서 주문수량을 넣으면 수량·손익 계산'} flush>
                  {simResult.trades ? (
                    <>
                      <table className="grid">
                        <thead>
                          <tr>
                            <th>구분</th>
                            <th className="num">체결일</th>
                            <th className="num">가격</th>
                            <th className="num">수량</th>
                            <th className="num">손익</th>
                          </tr>
                        </thead>
                        <tbody>
                          {simResult.trades.buys.map((t) => (
                            <tr key={`b${t.stage}`}>
                              <td className="up">매수 {t.stage}차</td>
                              <td className="num">{t.time}</td>
                              <td className="num">{fmtPrice(t.price)}</td>
                              <td className="num">{t.shares.toLocaleString()}주</td>
                              <td className="num">-</td>
                            </tr>
                          ))}
                          {simResult.trades.sells.map((t) => (
                            <tr key={`s${t.stage}`}>
                              <td className="down">매도 {t.stage}차</td>
                              <td className="num">{t.time}</td>
                              <td className="num">{fmtPrice(t.price)}</td>
                              <td className="num">{t.shares.toLocaleString()}주</td>
                              <td className={`num ${chgClass(t.pnl_pct)}`}>
                                {t.pnl != null && `${t.pnl > 0 ? '+' : ''}${Math.round(t.pnl).toLocaleString()}원`}
                                <small style={{ display: 'block' }}>{fmtChg(t.pnl_pct)}</small>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      <div className="sumcard">
                        <div className="pills">
                          <span>평단 <b>{simResult.trades.avg_entry != null ? fmtPrice(simResult.trades.avg_entry) : '-'}</b></span>
                          <span>실현 <b className={chgClass(simResult.trades.realized_pnl)}>{Math.round(simResult.trades.realized_pnl).toLocaleString()}원</b></span>
                          <span>
                            잔여 <b>{simResult.trades.remain_shares.toLocaleString()}주</b> 평가{' '}
                            <b className={chgClass(simResult.trades.unrealized_pnl)}>{Math.round(simResult.trades.unrealized_pnl).toLocaleString()}원</b>
                          </span>
                        </div>
                      </div>
                      <p className="hint" style={{ padding: '0 16px 10px' }}>
                        수수료·세금·슬리피지 미포함 — 정식 손익은 백테스트 엔진(ADR-0004) 몫.
                      </p>
                    </>
                  ) : (
                    <p className="hint" style={{ padding: '0 16px 10px' }}>
                      ② 매매전략의 주문수량(수량/금액)을 입력하고 다시 실행하세요.
                    </p>
                  )}
                </Card>
              )}

              <Card title="분할 매수" sub="②와 같은 값 — 여기서 고쳐도 됨">
                <BuyStages stages={draft.buy} computed={computed} onChange={(b) => set('buy', b)} />
              </Card>
              <Card title="분할 매도">
                <SellStages stages={draft.sell} computed={computed} onChange={(s) => set('sell', s)} />
              </Card>
            </div>
            <div className="sim-chart">
              <ProChart ref={proRef} />
              {/* 하단 결과 스트립 — "결국 결과가 어떻게 될거다"까지 차트 밑에서 (오너 지시) */}
              {simResult && <SimFoot r={simResult} />}
            </div>
          </div>
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
