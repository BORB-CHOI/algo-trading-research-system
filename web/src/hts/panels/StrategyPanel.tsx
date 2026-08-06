import { useEffect, useMemo, useRef, useState } from 'react'
import {
  fetchConditions,
  fetchStrategies,
  postSimulate,
  runScreen,
  type ConditionCategory,
  type ConditionDef,
  type ConditionParamDef,
  type ScreenResponse,
  type SimulateResponse,
  type StrategyDef,
} from '../../api'
import { ProChart, type ProChartHandle } from '../../ProChart'
import { allVisible, type OverlayVisibility } from '../../simVisibility'
import { currentSymbol, onSymbolPick, pickStrategy, pickSymbol, type SymbolPick } from '../bus'
import { chgClass, fmtChg, fmtEok, fmtPrice } from '../format'
import { MiniCandles } from '../MiniCandles'
import { BuyStages, SellStages, type ComputedPrices } from './SplitStages'
import {
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
  type SellBasis,
  type Strategies,
  type StrategyDraft,
} from './strategyStore'

// 전략 화면은 3단계다.
//  ① 종목선정 — 조건검색식을 여러 개 만들고 고친다 (저장소: hts-screens)
//  ② 매매전략 — 그중 하나를 골라 분할 매수/매도·주문조건을 붙인다 (저장소: hts-strategies)
//  ③ 시뮬레이션 — 대표 종목에 전략 1호(상승장 사이클+분할)를 돌려 전용 차트로 확인 (오너 지시:
//    종목 차트 오버레이 ❌, 이 탭에서 본다)
// 정량 값은 전부 이 화면에서 입력한다 (ADR-0009 — 전략 숫자 하드코딩 금지).

const LIMIT = 100

// ③ 시뮬레이션 대표 종목 — 오너 지시(2026-08-05)로 고정. 전략 설계 확인용 기준 종목이다.
const SIM_SYM = { code: '005930', name: '삼성전자', market: 'KOSPI' } as const

// ③ 예시 기본값 — 지위는 PLACEHOLDER 와 같다 (ADR-0009: 서버 하드코딩 금지, UI 예시는 허용).
// 실행 시 **빈 항목만** 이 값으로 채우고, 채운 값은 전부 화면(분할 카드·메시지)에 보인다.
// "실행 버튼을 누르면 무조건 예시가 보여야 한다"(오너) — 빈 폼 때문에 실행을 막지 않는다.
const SIM_EXAMPLE = {
  cycleDropPct: 50, // 오너도 -50/-60 미확정 — 화면에서 조정하는 값이다(ADR-0013)
  srSpan: 10, // 지지/저항 고점·저점 기준(좌우 거래일, ADR-0014)
  srClusterPct: 1,
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
function todayStr(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

// ③ 차트 요소별 표시 필터 — 겹칠 때 하나씩 끄고 본다 (오너 지시 2026-08-06).
const SIM_LAYERS: readonly (readonly [keyof OverlayVisibility, string])[] = [
  ['anchor', '앵커'],
  ['fib', '피보나치'],
  ['sr', '지지저항'],
  ['buy', '매수'],
  ['sell', '매도'],
  ['stop', '손절'],
  ['fills', '체결'],
] as const

const PLACEHOLDER: Record<string, string> = {
  short: '5',
  mid: '20',
  long: '60',
  period: '20',
  days: '250',
  within: '3',
  drop_pct: '50',
  sr_span: '10',
  sr_cluster_pct: '1',
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

const SELL_BASIS_LABEL = { avg_entry: '매수 평단', lowest_fill: '최저 체결가', anchor_high: '사이클 고점' } as const

// 매도 기준점 선택 — ②와 ③ 양쪽에 노출한다. ②에만 묻어두면 ③에서 반등률을 만지는
// 동안 기준점이 뭔지 안 보여 "평단 +10%가 왜 사이클 고점 값이냐"가 재발한다(2026-08-06).
function SellBasisPicker({ value, onChange }: { value: SellBasis; onChange: (b: SellBasis) => void }) {
  return (
    <div className="kv">
      <span className="k">매도 기준점</span>
      <span className="v">
        <span className="radios" style={{ marginLeft: 'auto' }}>
          {(Object.entries(SELL_BASIS_LABEL) as [SellBasis, string][]).map(([k, label]) => (
            <label key={k}>
              <input type="radio" checked={value === k} onChange={() => onChange(k)} />
              {label}
            </label>
          ))}
        </span>
      </span>
    </div>
  )
}

// ③ 차트 하단 결과 스트립 — 사이클·지지선·매도 기준·최종 손익을 한 줄씩. 차트와 같이 읽는 용도.
function SimFoot({ r, sellBasis }: { r: SimulateResponse; sellBasis: keyof typeof SELL_BASIS_LABEL }) {
  const stopLine = r.lines.find((l) => l.kind === 'stop')
  const srCount = r.lines.filter((l) => l.kind === 'sr').length
  const sellLines = r.lines.filter((l) => l.kind === 'sell')
  const buys = r.fills.filter((f) => f.side === 'buy').length
  const sells = r.fills.filter((f) => f.side === 'sell').length
  const t = r.trades
  const total = t ? t.realized_pnl + t.unrealized_pnl : null
  return (
    <div className="sim-foot">
      <p>
        <b>상승장</b> {r.cycle.low_date} {fmtPrice(r.cycle.low_price)} → {r.cycle.high_date}{' '}
        {fmtPrice(r.cycle.high_price)} (+{r.cycle.gain_pct.toFixed(0)}%)
        {r.cycle.is_52w_high ? ' · 고점 = 52주 신고가' : ''}
        {r.cycle.confirmed
          ? ` · -${r.cycle.drop_pct}% 하락 후 바닥`
          : ` · -${r.cycle.drop_pct}% 하락 없음 — 구간 최저가로 대신`}
      </p>
      {/* 매도 기준가를 명시한다 — 안 보이면 "평단 기준인 줄 알았다"가 반복된다 (2026-08-06). */}
      {sellLines.length > 0 && (
        <p>
          <b>매도 기준</b> {SELL_BASIS_LABEL[sellBasis]}
          {r.sell_basis_price != null && <> {fmtPrice(r.sell_basis_price)}</>} 대비 반등 —{' '}
          {sellLines.map((l) => `${l.label} ${fmtPrice(l.price)}`).join(' · ')}
        </p>
      )}
      <p>
        <b>지지저항</b> 수평선 {srCount}개 (좌우 며칠보다 튀어나온 고점·저점, 비슷한 가격은
        한 선) — 매수·매도는 각 레벨에서 가장 가까운 선에 걸린다 · 사이클 저점{' '}
        {fmtPrice(r.cycle.low_price)}
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

  const [date, setDate] = useState(todayStr) // 기준일 기본 = 오늘 (오너 지시 2026-08-06)
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

  // ── ③ 시뮬레이션 — 전략 1호(상승장 사이클 + 분할). 계산은 전부 파이썬(/api/simulate) ──
  //
  // 종목은 **대표 종목(삼성전자) 고정**이다 — ① 에서 뭘 골랐는지와 무관 (오너 지시 2026-08-05:
  // "내가 설계한 전략이 어떻게 되는지만 보고 싶은 거"). 이 화면은 전략 설계를 눈으로
  // 확인하는 자리지 종목 검증 자리가 아니다. 실전 적용은 백테스트 러너(ADR-0007) 몫.
  //
  // 파동 입력은 사이클 하락 기준 하나뿐이다 — "급등" 개념은 없다 (오너 확정 2026-08-06).
  // 빈 값은 예시값으로 채워서 실행이 절대 막히지 않게 한다 — 채운 값은 화면에 보인다.
  const proRef = useRef<ProChartHandle>(null)
  const [simDate, setSimDate] = useState(todayStr)
  const [simMsg, setSimMsg] = useState('')
  const [simRunning, setSimRunning] = useState(false)
  const [simResult, setSimResult] = useState<SimulateResponse | null>(null)
  const [computed, setComputed] = useState<ComputedPrices>({})
  const [simVis, setSimVis] = useState<OverlayVisibility>(allVisible)

  function toggleLayer(k: keyof OverlayVisibility) {
    const next = { ...simVis, [k]: !simVis[k] }
    setSimVis(next)
    proRef.current?.setOverlayVisibility(next)
  }

  // 최신 결과를 ref 로도 들고 있는다 — 탭 재진입 효과가 simResult 를 deps 에 넣으면
  // 실행할 때마다 showSymbol(데이터 재로드)이 돌아 줌이 풀리기 때문이다.
  const simResultRef = useRef<SimulateResponse | null>(null)
  useEffect(() => {
    simResultRef.current = simResult
  }, [simResult])
  const simVisRef = useRef(simVis)
  useEffect(() => {
    simVisRef.current = simVis
  }, [simVis])

  // ③ 에 들어오면(차트가 새로 마운트되면) 대표 종목을 싣고, 직전 결과가 있으면 다시 그린다.
  useEffect(() => {
    if (step !== 'sim') return
    proRef.current?.showSymbol(SIM_SYM.code, SIM_SYM.name, SIM_SYM.market)
    const r = simResultRef.current
    if (r) proRef.current?.applySimulation({ lines: r.lines, fills: r.fills, series: r.series })
    // 차트가 새로 마운트되면 필터는 전체 표시로 초기화된다 — 이전 선택을 다시 입힌다.
    proRef.current?.setOverlayVisibility(simVisRef.current)
  }, [step])

  async function runSimulation() {
    // 빈 값 때문에 실행을 막지 않는다 — 예시값으로 채우고, 채운 사실을 메시지로 알린다.
    const filled: string[] = []

    let buy = draft.buy
    const usable = buy.filter((b) => b.enabled && b.ratio > 0 && b.ratio < 1)
    if (usable.length === 0) {
      // 차수 자체가 없다 → 예시 3차로 시작한다.
      buy = SIM_EXAMPLE.buy.map((b) => newBuyStage(b.ratio, b.weight))
      set('buy', buy)
      filled.push('분할 매수 3차(38.2/50/61.8%)')
    } else if (!usable.some((b) => b.weight > 0)) {
      // 차수는 사용자가 만들었는데 비중만 비었다 → 사용자의 되돌림은 두고 비중만 균등하게.
      const each = Math.floor(100 / usable.length)
      const ids = new Set(usable.map((b) => b.id))
      buy = buy.map((b) => (ids.has(b.id) ? { ...b, weight: each } : b))
      set('buy', buy)
      filled.push(`비중 균등 ${each}%씩`)
    }

    // 파동·지지저항 기준은 ② 진입 기법(피보나치)의 파라미터다 (오너: "피보나치에만
    // 해당되는 거잖아, 옮겨"). 비었으면 예시값으로 돌리고 채운 사실을 알린다.
    const ep = draft.entryKey === 'fib_retrace' ? draft.entryParams : {}
    let cyc = Number(ep['drop_pct'])
    if (!(cyc > 0 && cyc < 100)) {
      cyc = SIM_EXAMPLE.cycleDropPct
      filled.push(`사이클 하락 기준 ${cyc}%`)
    }
    let srSpan = Number(ep['sr_span'])
    if (!(Number.isInteger(srSpan) && srSpan >= 1)) {
      srSpan = SIM_EXAMPLE.srSpan
      filled.push(`고점·저점 기준 ${srSpan}일`)
    }
    let srCluster = Number(ep['sr_cluster_pct'])
    if (!(srCluster > 0)) {
      srCluster = SIM_EXAMPLE.srClusterPct
      filled.push(`같은 선 폭 ${srCluster}%`)
    }
    const buyOff = Number(draft.buyTickOffset || '0')
    const sellOff = Number(draft.sellTickOffset || '0')

    const hasQty = Number(draft.qty) > 0
    const qty = hasQty ? Number(draft.qty) : SIM_EXAMPLE.qtyShares
    if (!hasQty) filled.push(`수량 ${SIM_EXAMPLE.qtyShares}주`)

    setSimRunning(true)
    setSimMsg('계산 중…')
    try {
      const res = await postSimulate({
        code: SIM_SYM.code,
        end: simDate || undefined,
        cycle_drop_pct: cyc,
        sr_span: srSpan,
        sr_cluster_pct: srCluster,
        buy: buy.map((b) => ({
          id: b.id, ratio: b.ratio, weight: b.weight, enabled: b.enabled, price_override: b.priceOverride,
        })),
        sell: draft.sell.map((s) => ({
          id: s.id, rebound_pct: s.reboundPct, weight: s.weight, enabled: s.enabled, price_override: s.priceOverride,
        })),
        sell_basis: draft.sellBasis,
        buy_tick_offset: Number.isInteger(buyOff) ? buyOff : 0,
        sell_tick_offset: Number.isInteger(sellOff) ? sellOff : 0,
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
      // 경고(못 건 목표가 등)는 그려진 결과와 함께 보여준다 — 오류만 띄우고 빈 화면 ❌.
      const notes = [
        ...res.warnings,
        ...(filled.length ? [`예시값 사용: ${filled.join(' · ')}`] : []),
      ]
      setSimMsg(notes.join(' / '))
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
              <Card title="조건 만들기" sub="카테고리 → 조건 → 값 입력">
                {/* 증권사 조건검색처럼 한 계층씩 — 카테고리 줄, 그 카테고리의 조건 줄.
                    빠른선택 칩 + 카테고리/조건 드롭다운의 이중 구조는 삭제 (오너 지적 2026-08-06). */}
                <div className="chips" style={{ marginBottom: 8 }}>
                  {condCats.map((c) => (
                    <button
                      key={c.key}
                      className={`chip ${catKey === c.key ? 'on' : ''}`}
                      onClick={() => {
                        setCatKey(c.key)
                        setCondKey('')
                        setCondDraft({})
                        setScreenErr('')
                      }}
                    >
                      {c.name}
                    </button>
                  ))}
                </div>
                <div className="chips" style={{ marginBottom: 10 }}>
                  {catConds
                    .filter((c) => c.key !== 'new_low')
                    .map((c) => {
                      // 신고가/신저가는 증권사처럼 한 항목 — 구분은 고른 뒤 라디오로 (오너 지시).
                      const merged = c.key === 'new_high'
                      const on = merged
                        ? condKey === 'new_high' || condKey === 'new_low'
                        : condKey === c.key
                      return (
                        <button
                          key={c.key}
                          className={`chip ${on ? 'on' : ''}`}
                          title={c.desc}
                          onClick={() => {
                            if (!on) pickCondition(c.key)
                          }}
                        >
                          {merged ? '신고가/신저가' : c.name}
                        </button>
                      )
                    })}
                  {catConds.length === 0 && <span className="hint">카테고리를 고르세요.</span>}
                </div>

                {/* 자리를 미리 잡아둔다 — 조건을 바꿔도 아래가 안 움직인다 */}
                <div className="paramzone" style={{ '--rows': maxParams } as React.CSSProperties}>
                  {condDef ? (
                    <>
                      <p className="hint">
                        {condDef.desc}
                        {(condKey === 'new_high' || condKey === 'new_low') &&
                          ' · 52주 ≈ 250거래일 (기간+이내 ≤ 520)'}
                      </p>
                      {(condKey === 'new_high' || condKey === 'new_low') && (
                        <div className="kv">
                          <span className="k">구분</span>
                          <span className="v">
                            <span className="radios" style={{ marginLeft: 'auto' }}>
                              <label>
                                <input
                                  type="radio"
                                  checked={condKey === 'new_high'}
                                  onChange={() => setCondKey('new_high')}
                                />
                                신고가
                              </label>
                              <label>
                                <input
                                  type="radio"
                                  checked={condKey === 'new_low'}
                                  onChange={() => setCondKey('new_low')}
                                />
                                신저가
                              </label>
                            </span>
                          </span>
                        </div>
                      )}
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

              {/* "종목 상세" 카드는 삭제 (오너 지적 2026-08-05) — 종목을 뜯어보는 자리는
                  우측 종목 드로어·차트 탭이지 검색식 만드는 화면이 아니다. */}
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
                    title="기준일 (기본 = 오늘, 휴장일이면 직전 거래일 기준)"
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
                  {/* 차트 적용 버튼 — 화면 개편 때 pickStrategy 호출부가 통째로 사라져
                      "설정해도 차트에 아무것도 안 뜨는" 상태였다 (오너 지적 2026-08-06 복원). */}
                  <div className="form-row" style={{ marginTop: 8 }}>
                    <button
                      className="primary"
                      onClick={() => {
                        const r = parseParams(entryDef.params, draft.entryParams)
                        if (!r.ok) {
                          setEntryErr(r.error)
                          return
                        }
                        setEntryErr('')
                        pickStrategy({
                          key: entryDef.key,
                          params: r.value,
                          signals: entryDef.signals,
                          overlay: entryDef.overlay,
                        })
                        setMsg(`[${entryDef.name}] 적용 — 차트 탭에서 확인하세요.`)
                      }}
                    >
                      차트에 적용
                    </button>
                    <button
                      onClick={() => {
                        pickStrategy(null)
                        setMsg('차트 오버레이 해제')
                      }}
                    >
                      해제
                    </button>
                  </div>
                </>
              ) : (
                <p className="hint">기법을 선택하면 파라미터 입력 폼이 나옵니다.</p>
              )}
              {entryErr && <p className="hint warn">{entryErr}</p>}
            </Card>

            <Card title="분할 매수" sub="되돌림 레벨에서 가장 가까운 지지/저항선에 건다 (ADR-0014)">
              <BuyStages stages={draft.buy} computed={computed} onChange={(b) => set('buy', b)} />
              <div className="kv" style={{ marginTop: 8 }}>
                <span className="k">호가 오프셋</span>
                <span className="v">
                  <input
                    className="amt"
                    placeholder="0"
                    value={draft.buyTickOffset}
                    onChange={(e) => set('buyTickOffset', e.target.value)}
                  />
                  <span className="unit">호가</span>
                </span>
              </div>
              <p className="hint">선택된 지지/저항선에서 몇 호가 위(+)/아래(−)에 걸지. 0 = 선 그대로.</p>
            </Card>

            <Card title="분할 매도" sub="기준점 대비 반등률">
              <SellBasisPicker value={draft.sellBasis} onChange={(b) => set('sellBasis', b)} />
              <SellStages stages={draft.sell} computed={computed} onChange={(s) => set('sell', s)} />
              <div className="kv" style={{ marginTop: 8 }}>
                <span className="k">호가 오프셋</span>
                <span className="v">
                  <input
                    className="amt"
                    placeholder="0"
                    value={draft.sellTickOffset}
                    onChange={(e) => set('sellTickOffset', e.target.value)}
                  />
                  <span className="unit">호가</span>
                </span>
              </div>
              <p className="hint">반등 목표가에서 가장 가까운 기준가 위 지지/저항선 ± 오프셋에 건다.</p>
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
                            onChange={(e) => set('stopSource', e.target.value as 'cycle_low' | 'custom')}
                          >
                            <option value="cycle_low">사이클 저점 (피보 시작점)</option>
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
                {/* 입력 = 전략 선택 + 기준일 + 사이클 하락 기준. 빈 값은 예시로 채워 실행한다. */}
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
                      title="기본 = 오늘 (휴장일이면 직전 거래일 기준). 과거 날짜를 주면 그 시점을 재현한다."
                    />
                  </span>
                </div>
                <div className="kv">
                  <span className="k">파동·지지저항</span>
                  <span className="v">
                    사이클 -{Number(draft.entryKey === 'fib_retrace' ? draft.entryParams['drop_pct'] : 0) || SIM_EXAMPLE.cycleDropPct}%
                    · 고점·저점 {Number(draft.entryKey === 'fib_retrace' ? draft.entryParams['sr_span'] : 0) || SIM_EXAMPLE.srSpan}일
                    · 같은 선 폭 {Number(draft.entryKey === 'fib_retrace' ? draft.entryParams['sr_cluster_pct'] : 0) || SIM_EXAMPLE.srClusterPct}%
                  </span>
                </div>
                <p className="hint">기준 수정은 ② 매매전략의 진입 기법(피보나치)에서 — 여기는 실행만.</p>
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
                        <td className="flat">상승장 저점</td>
                        <td className="num">
                          {simResult.cycle.low_date} {fmtPrice(simResult.cycle.low_price)}
                          {!simResult.cycle.confirmed && (
                            <small style={{ display: 'block' }}>-{simResult.cycle.drop_pct}% 하락 없음 — 구간 최저가</small>
                          )}
                        </td>
                      </tr>
                      <tr>
                        <td className="flat">상승장 고점</td>
                        <td className="num">
                          {simResult.cycle.high_date} {fmtPrice(simResult.cycle.high_price)}
                          {simResult.cycle.is_52w_high ? ' (52주 신고가)' : ''}
                        </td>
                      </tr>
                      <tr>
                        <td className="flat">매도 기준가</td>
                        <td className="num">
                          {simResult.sell_basis_price != null ? fmtPrice(simResult.sell_basis_price) : '-'}
                        </td>
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

              {/* 분할 매수/매도 카드는 ③에서 삭제 (오너 지시 2026-08-06) —
                  "차트만 잘 보여주면 돼. 요약 정보와 체결 내역만 있으면 돼." 설정은 ②에서. */}
            </div>
            <div className="sim-chart">
              {/* 요소별 필터 — 겹칠 때 하나씩 끄고 본다 (오너 지시 2026-08-06). 줌은 유지된다. */}
              <div className="chips sim-layers">
                {SIM_LAYERS.map(([k, label]) => (
                  <button
                    key={k}
                    className={`chip ${simVis[k] ? 'on' : ''}`}
                    title={simVis[k] ? '숨기기' : '표시'}
                    onClick={() => toggleLayer(k)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <div className="sim-canvas">
                <ProChart ref={proRef} />
              </div>
              {/* 하단 결과 스트립 — "결국 결과가 어떻게 될거다"까지 차트 밑에서 (오너 지시) */}
              {simResult && <SimFoot r={simResult} sellBasis={draft.sellBasis} />}
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
