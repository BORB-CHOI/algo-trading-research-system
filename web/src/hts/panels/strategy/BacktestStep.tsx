import { useEffect, useRef, useState } from 'react'
import type { Dispatch, ReactNode, SetStateAction } from 'react'
import {
  deleteRun,
  fetchBacktestAll,
  fetchRunResult,
  fetchRuns,
  postBacktest,
  postBacktestAll,
  postSimulate,
  type BacktestRequest,
  type BacktestResponse,
  type BacktestRow,
  type SavedRun,
  type SimulateResponse,
} from '../../../api'
import { ProChart, type ProChartHandle } from '../../../ProChart'
import { chgClass, fmtPrice } from '../../format'
import { Modal } from '../../components/Modal'
import { Card, KV, MsgLine } from '../../components/ui'
import { BAND_PAYLOAD, SR_PAYLOAD, START_PAYLOAD, ZZ_PAYLOAD } from '../strategyOne'
import { newBuyStage, toDraft, type Strategies, type StrategyDraft } from '../strategyStore'
import { SIM_EXAMPLE, stopPayload, todayStr } from './common'

// ④ 백테스팅 — 전수 검사 (layer4 strategy_one). 전략 값은 ②의 현재 값(draft)을 쓴다.
// StrategyPanel.tsx 분할(구조 리팩토링 2026-08-06)로 옮겨온 스텝.

// ④ 구간 표시용 — 정본은 layer4 backtest.SPLITS (§4.1 3분할). 여기는 라벨만.
// 'all' 만 성격이 다르다: 구간을 자르는 게 아니라 **거래일마다 종목을 다시 고르는** 검사다
// (layer4 walk_forward, 오너 2026-08-10: "그때부터 하루씩 지금까지 매매 가능해야지").
const SPLIT_LABEL = {
  all: '전 기간 — 날마다 종목을 다시 고른다 (몇 분)',
  train: '값 맞추기용 2020-01-01 ~ 2023-12-31',
  validate: '맞춘 값 확인용 2024-01-01 ~ 2024-12-31',
  test: '마지막 채점용 2025-01-01 ~ 2025-12-31 (딱 한 번)',
} as const
type SplitKey = keyof typeof SPLIT_LABEL

// 전 기간 검사의 기본 시작일. 오너 2026-08-10: "2019년부터 26년 8월 10일까지라니까".
// 끝은 오늘(todayStr) — 데이터가 있는 마지막 거래일까지 서버가 알아서 자른다.
const ALL_START_DEFAULT = '2019-01-01'

/** 행에서 바로 여는 차트 — **④ 화면을 떠나지 않는다.**
 *
 *  오너 2026-08-10: "행 클릭해서 시뮬레이션 누르니까 시뮬레이션 페이지로 가고 다 초기화
 *  되잖아. 나는 백테스트 화면에서 간략한 화면으로 보고 싶은 거라고. 딱 차트랑 선, 지표,
 *  타점 그런 것만." — 설정 패널·종목 검색·기준일 입력 없이 그림만 띄운다.
 *
 *  값은 ④가 검사에 쓴 것과 **같은 것**을 보낸다(전략 1호 고정 정의 + 고른 전략의 분할·손절).
 *  ③처럼 파동 값을 손으로 돌리지 않는다 — 그러면 표의 숫자와 그림이 어긋난다. */
function RowChart(props: {
  readonly row: BacktestRow
  readonly planDate: string
  readonly draft: StrategyDraft
  readonly onClose: () => void
}) {
  const { row: r, planDate, draft, onClose } = props
  const proRef = useRef<ProChartHandle>(null)
  const [msg, setMsg] = useState('그리는 중…')
  const [sim, setSim] = useState<SimulateResponse | null>(null)
  // 차트 데이터가 들어오기 전에 그리면 아무것도 안 보인다 — 둘 다 준비됐을 때만 그린다.
  const readyRef = useRef(false)
  const simRef = useRef<SimulateResponse | null>(null)
  const drawnRef = useRef(false)
  // 무엇을 보여줄 범위인가. 기본은 **전 기간** — 기준일까지만 보면 정작 궁금한 것(그래서
  // 언제 샀고 언제 팔았나)이 화면 밖에 있다(오너 2026-08-10).
  const [range, setRange] = useState<'all' | 'plan'>('all')

  /** 기준일이 화면에 들어오도록 과거 데이터를 끌어온 뒤 범위를 맞춘다.
   *  `showUntil` 이 그 날짜까지 안 받아온 구간을 다시 받아 온다 — 이걸 먼저 해야
   *  '전 기간'에서 파동 바닥(수년 전일 수 있다)이 화면에 들어온다. */
  async function fit(mode: 'all' | 'plan') {
    if (mode === 'all') {
      // 먼저 기준일까지 끌어온 **뒤에** 전체로 편다 — 순서가 바뀌면 다시 그 날짜로
      // 스크롤돼서 뒤쪽(언제 샀고 팔았나)이 화면 밖에 남는다.
      await proRef.current?.showUntil(planDate)
      proRef.current?.setVisibleBars(0) // 0 = 받아온 봉 전부
      return
    }
    proRef.current?.setVisibleBars(500)
    await proRef.current?.showUntil(planDate)
  }

  function draw() {
    if (drawnRef.current || !readyRef.current || !simRef.current) return
    drawnRef.current = true
    const s = simRef.current
    void fit(range)
    proRef.current?.applySimulation({ lines: s.lines, fills: s.fills, series: s.series })
  }

  useEffect(() => {
    let alive = true
    postSimulate({
      code: r.code,
      end: planDate,
      ...START_PAYLOAD,
      ...ZZ_PAYLOAD,
      ...BAND_PAYLOAD,
      ...SR_PAYLOAD,
      buy: draft.buy.map((b) => ({ id: b.id, ratio: b.ratio, weight: b.weight, enabled: b.enabled })),
      sell: draft.sell.map((s) => ({
        id: s.id, rebound_pct: s.reboundPct, weight: s.weight, enabled: s.enabled,
      })),
      sell_basis: draft.sellBasis,
      buy_tick_offset: Number(draft.buyTickOffset || '0') || 0,
      sell_tick_offset: Number(draft.sellTickOffset || '0') || 0,
      buy_min_gap_pct: Number(draft.buyMinGapPct || '0') || 0,
      stop: stopPayload(draft),
    })
      .then((res) => {
        if (!alive) return
        simRef.current = res
        setSim(res)
        setMsg(res.warnings.join(' / '))
        draw()
      })
      .catch((e: unknown) => {
        if (alive) setMsg(e instanceof Error ? e.message : '차트를 그리지 못했습니다')
      })
    return () => {
      alive = false
    }
    // 한 번만 부른다 — 이 모달은 행 하나에 대해 열렸다 닫힌다(열쇠가 바뀌면 새로 마운트).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <Modal
      open
      onClose={onClose}
      className="chart"
      width={1180}
      title={`${r.name || r.code} ${r.code} — ${planDate} 기준`}
    >
      <div className="bt-chart-canvas">
        <ProChart
          ref={proRef}
          initialSymbol={{ code: r.code, name: r.name || r.code, market: '' }}
          hideToolbar
          onBaseBar={() => {
            // 봉이 들어왔다는 신호 — 이때부터 showUntil·오버레이가 먹는다.
            readyRef.current = true
            draw()
          }}
        />
      </div>
      <div className="bt-chart-note">
        {sim ? (
          <>
            <span>
              파동 <b>{fmtPrice(r.wave_low ?? 0)}</b> ({r.wave_low_date}) → <b>{fmtPrice(r.wave_high ?? 0)}</b>
            </span>
            <span>산 횟수 <b>{r.n_buys}번</b></span>
            {r.avg_entry != null && <span>평단 <b>{fmtPrice(r.avg_entry)}</b></span>}
            {r.exit_value != null && <span>판 값 <b>{fmtPrice(r.exit_value)}</b></span>}
            {r.net_return != null && (
              <span>
                수익률 <b className={chgClass(r.net_return)}>{(r.net_return * 100).toFixed(1)}%</b>
              </span>
            )}
            {r.stopped && <span className="chip warn">손절</span>}
            {r.open && <span className="chip">안 팔림</span>}
          </>
        ) : (
          <span>{msg}</span>
        )}
        {sim && msg && <span className="warn">{msg}</span>}
        <span style={{ marginLeft: 'auto' }} className="radios">
          <label>
            <input
              type="radio"
              checked={range === 'all'}
              onChange={() => {
                setRange('all')
                void fit('all')
              }}
            />
            전 기간
          </label>
          <label>
            <input
              type="radio"
              checked={range === 'plan'}
              onChange={() => {
                setRange('plan')
                void fit('plan')
              }}
            />
            고른 날까지
          </label>
        </span>
      </div>
    </Modal>
  )
}

/** 결과 표 거르기·정렬 — 전 기간 검사는 줄이 수천 개라 눈으로 훑을 수 없다. */
type RowFilter = 'all' | 'win' | 'lose' | 'stopped' | 'open' | 'closed'
type SortKey = 'net_desc' | 'net_asc' | 'date_asc' | 'date_desc' | 'code'

const FILTER_LABEL: Record<RowFilter, string> = {
  all: '전부',
  win: '번 것',
  lose: '잃은 것',
  stopped: '손절',
  open: '안 팔린 것',
  closed: '다 판 것',
}

const SORT_LABEL: Record<SortKey, string> = {
  net_desc: '수익률 높은 순',
  net_asc: '수익률 낮은 순',
  date_asc: '산 날 빠른 순',
  date_desc: '산 날 늦은 순',
  code: '종목코드 순',
}

function matches(r: BacktestRow, f: RowFilter, needle: string): boolean {
  if (needle && !`${r.name ?? ''} ${r.code}`.toLowerCase().includes(needle)) return false
  switch (f) {
    case 'win':
      return (r.net_return ?? 0) > 0
    case 'lose':
      return (r.net_return ?? 0) <= 0
    case 'stopped':
      return !!r.stopped
    case 'open':
      return !!r.open
    case 'closed':
      return !r.open
    default:
      return true
  }
}

function sortRows(rows: BacktestRow[], by: SortKey): BacktestRow[] {
  const key = (r: BacktestRow) => r.plan_date ?? r.first_fill ?? ''
  const copy = [...rows] // 원본을 건드리지 않는다 — 서버 응답은 그대로 둔다
  switch (by) {
    case 'net_asc':
      return copy.sort((a, b) => (a.net_return ?? 0) - (b.net_return ?? 0))
    case 'date_asc':
      return copy.sort((a, b) => key(a).localeCompare(key(b)))
    case 'date_desc':
      return copy.sort((a, b) => key(b).localeCompare(key(a)))
    case 'code':
      return copy.sort((a, b) => a.code.localeCompare(b.code))
    default:
      return copy.sort((a, b) => (b.net_return ?? 0) - (a.net_return ?? 0))
  }
}

/** 페이지 넘기기 줄. 전부 볼 수 있다는 게 핵심이라 총 개수를 항상 보여준다. */
function Pager(props: {
  readonly page: number
  readonly pages: number
  readonly total: number
  readonly perPage: number
  readonly onPage: (n: number) => void
  readonly onPerPage: (n: number) => void
}) {
  const { page, pages, total, perPage, onPage, onPerPage } = props
  return (
    <div className="bt-pager">
      <span className="dim">
        전체 <b>{total.toLocaleString()}</b>줄 · {pages.toLocaleString()}쪽 중 {page.toLocaleString()}쪽
      </span>
      <span style={{ marginLeft: 'auto' }}>
        <button type="button" disabled={page <= 1} onClick={() => onPage(1)}>맨 앞</button>
        <button type="button" disabled={page <= 1} onClick={() => onPage(page - 1)}>이전</button>
        <button type="button" disabled={page >= pages} onClick={() => onPage(page + 1)}>다음</button>
        <button type="button" disabled={page >= pages} onClick={() => onPage(pages)}>맨 뒤</button>
      </span>
      <select value={perPage} onChange={(e) => onPerPage(Number(e.target.value))}>
        {[50, 100, 200, 500].map((n) => (
          <option key={n} value={n}>{n}줄씩</option>
        ))}
      </select>
    </div>
  )
}

/** 행 열쇠 — 전 구간 검사는 한 종목이 라운드마다 한 줄씩 나오므로 고른 날까지 붙인다. */
function rowKey(r: BacktestRow): string {
  return r.plan_date ? `${r.code}@${r.plan_date}` : r.code
}

/** 종목 한 줄 + 펴면 나오는 근거. 오너 2026-08-09: "클릭하면 뭔 기준으로 어떻게 사고
 *  팔았는지. 얼마에 언제 샀고 얼마에 언제 팔았는지." */
function RowWithDetail(props: {
  readonly row: BacktestRow
  readonly open: boolean
  readonly onToggle: () => void
  readonly compact?: boolean
  /** 이 행의 차트를 그릴 기준일 — 이 종목을 고른 날. */
  readonly planDate?: string | null
  readonly onChart?: (row: BacktestRow, date: string) => void
}) {
  const { row: r, open, onToggle, compact, onChart } = props
  // 전 구간 검사는 라운드마다 고른 날이 다르다(plan_date). 구간 검사는 하나뿐(base_date).
  const planDate = r.plan_date ?? props.planDate ?? null
  const span = (r.wave_high ?? 0) - (r.wave_low ?? 0)
  const pctOf = (px: number) => (span > 0 ? ((r.wave_high! - px) / span) * 100 : null)
  return (
    <>
      <tr onClick={onToggle} style={{ cursor: 'pointer' }} title="누르면 근거가 펴집니다">
        <td>
          <span className="mono dim">{open ? '▾' : '▸'}</span> {r.name || r.code}{' '}
          <span className="dim">{r.code}</span>
          {r.stopped ? <span className="chip warn" style={{ marginLeft: 6 }}>손절</span> : null}
          {r.open ? <span className="chip" style={{ marginLeft: 6 }}>안 팔림</span> : null}
          {planDate ? <span className="dim" style={{ marginLeft: 6 }}>{planDate} 고름</span> : null}
        </td>
        {compact ? (
          <td colSpan={5} className="num dim">
            걸어 둔 값 {r.buy_orders?.map((o) => fmtPrice(o.price ?? 0)).join(' / ') ?? '없음'}
            {r.low_in_span != null && <> · 구간 최저가 <b>{fmtPrice(r.low_in_span)}</b></>}
          </td>
        ) : (
          <>
            <td className="num">{r.n_buys}번</td>
            <td className="num">{r.avg_entry != null ? fmtPrice(r.avg_entry) : '-'}</td>
            <td className="num">{r.exit_value != null ? fmtPrice(r.exit_value) : '-'}</td>
            <td className={`num ${chgClass(r.net_return ?? 0)}`}>
              {r.net_return != null ? `${(r.net_return * 100).toFixed(1)}%` : '-'}
            </td>
            <td className="num">{r.first_fill} ~ {r.last_exit}</td>
          </>
        )}
      </tr>
      {open && (
        <tr>
          <td colSpan={6} className="bt-detail">
            {/* 표의 숫자보다 차트가 빨리 말해 준다. 이 화면 위에 띄운다 — 다른 탭으로
                넘어가면 여기서 보던 게 다 날아간다(오너 2026-08-10). */}
            {onChart && planDate && (
              <div className="form-row" style={{ marginBottom: 8 }}>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation()
                    onChart(r, planDate)
                  }}
                >
                  차트로 보기 ({planDate} 기준)
                </button>
              </div>
            )}
            <div className="bt-detail-grid">
              <div>
                <h5>어디를 보고 걸었나</h5>
                <ul>
                  <li>
                    파동 바닥 <b>{fmtPrice(r.wave_low ?? 0)}</b>
                    <span className="dim"> ({r.wave_low_date})</span> → 꼭대기{' '}
                    <b>{fmtPrice(r.wave_high ?? 0)}</b>
                  </li>
                  {r.sell_basis_price != null && (
                    <li>매도 기준가(평단) <b>{fmtPrice(r.sell_basis_price)}</b></li>
                  )}
                </ul>
                <h5>걸어 둔 값</h5>
                <ul>
                  {r.buy_orders?.map((o) => (
                    <li key={`b${o.tranche}`}>
                      매수 {o.tranche}번째 · 되돌림{' '}
                      {o.ratio != null ? `${(o.ratio * 100).toFixed(1)}%` : '-'} →{' '}
                      <b>{fmtPrice(o.price ?? 0)}</b>
                      {pctOf(o.price ?? 0) != null && (
                        <span className="dim"> (실제 {pctOf(o.price ?? 0)!.toFixed(1)}% 자리)</span>
                      )}
                    </li>
                  ))}
                  {r.sell_orders?.map((o) => (
                    <li key={`s${o.tranche}`}>
                      매도 {o.tranche}번째 · 반등 {o.rebound_pct}% →{' '}
                      {o.price != null ? <b>{fmtPrice(o.price)}</b> : <span className="dim">걸 자리 없음</span>}
                    </li>
                  ))}
                  {r.stop_price != null && (
                    <li>
                      손절 → <b>{fmtPrice(r.stop_price)}</b>
                      {pctOf(r.stop_price) != null && (
                        <span className="dim"> (되돌림 {pctOf(r.stop_price)!.toFixed(1)}% 자리)</span>
                      )}
                    </li>
                  )}
                </ul>
              </div>
              <div>
                <h5>실제로 사고판 것</h5>
                {r.fills && r.fills.length > 0 ? (
                  <table className="grid tight">
                    <thead>
                      <tr>
                        <th>날짜</th>
                        <th>구분</th>
                        <th className="num">가격</th>
                        <th className="num">비중</th>
                      </tr>
                    </thead>
                    <tbody>
                      {[...r.fills]
                        .sort((a, b) => a.time.localeCompare(b.time))
                        .map((f, i) => (
                          <tr key={`${f.time}-${f.side}-${i}`}>
                            <td>{f.time}</td>
                            <td className={f.side === 'buy' ? 'up' : 'down'}>
                              {f.side === 'buy' ? '샀다' : '팔았다'}
                            </td>
                            <td className="num">{fmtPrice(f.price)}</td>
                            <td className="num dim">{f.w.toFixed(0)}</td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                ) : (
                  <p className="hint">
                    한 주도 안 걸렸습니다.
                    {r.low_in_span != null && (
                      <> 구간 최저가가 <b>{fmtPrice(r.low_in_span)}</b> 라 1번째 지정가{' '}
                      <b>{fmtPrice(r.buy_orders?.[0]?.price ?? 0)}</b> 까지 안 내려왔습니다.</>
                    )}
                  </p>
                )}
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

export function BacktestStep(props: {
  active: boolean
  catErrNode: ReactNode
  saved: Strategies
  name: string
  setName: Dispatch<SetStateAction<string>>
  draft: StrategyDraft
  setDraft: Dispatch<SetStateAction<StrategyDraft>>
  onGoStrategy: () => void
}) {
  const { active, catErrNode, saved, name, setName, draft, setDraft, onGoStrategy } = props
  // 행에서 연 차트 — 이 화면 위에 뜬다(탭 이동 ❌).
  const [chartFor, setChartFor] = useState<{ row: BacktestRow; date: string } | null>(null)
  // 결과 표 — 전 기간 검사는 수천 줄이 나온다. 잘라 버리지 않고 **거르고 나눠서** 전부 본다
  // (오너 2026-08-10: "전체를 볼 수 있게 해. 너무 길어서 문제면 페이지네이션과 필터 기능을").
  const [q, setQ] = useState('') // 종목명·코드
  const [only, setOnly] = useState<RowFilter>('all')
  const [sortBy, setSortBy] = useState<SortKey>('net_desc')
  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState(50)
  const [noFillShown, setNoFillShown] = useState(50)
  // 보관함 — 돌린 결과는 자동으로 담기는데 꺼낼 입구가 없었다(오너 2026-08-10).
  const [runs, setRuns] = useState<SavedRun[]>([])
  const [runId, setRunId] = useState('')
  const [loadedFrom, setLoadedFrom] = useState<string | null>(null)

  async function refreshRuns() {
    try {
      setRuns(await fetchRuns(100))
    } catch {
      // 보관함을 못 읽는다고 검사를 막지 않는다 — 목록만 빈 채로 둔다.
      setRuns([])
    }
  }

  // 탭에 들어올 때마다 목록을 새로 읽는다 — 방금 돌린 게 바로 보여야 한다.
  useEffect(() => {
    if (active) void refreshRuns()
  }, [active])

  async function loadRun(id: number) {
    setBtMsg('불러오는 중…')
    try {
      const res = await fetchRunResult(id)
      setBtResult(res)
      setOpenCode(null)
      setPage(1)
      setNoFillShown(50)
      setLoadedFrom(`${res.label || `${id}번`} · ${res.ran_at.slice(0, 16).replace('T', ' ')}`)
      setBtMsg('')
    } catch (e) {
      setBtMsg(e instanceof Error ? e.message : '불러오기 실패')
    }
  }

  async function removeRun(id: number) {
    try {
      await deleteRun(id)
      setRunId('')
      await refreshRuns()
    } catch (e) {
      setBtMsg(e instanceof Error ? e.message : '삭제 실패')
    }
  }

  // 기본은 전 기간이다 — 하루만 고르는 검사를 기본으로 두니 "2019~2026 검사"인 줄 알고
  // 24종목짜리 결과를 보게 됐다(오너 지적 2026-08-09·10).
  const [btSplit, setBtSplit] = useState<SplitKey>('all')
  const [btConfirmTest, setBtConfirmTest] = useState(false)
  const [btRunning, setBtRunning] = useState(false)
  const [btMsg, setBtMsg] = useState('')
  const [btResult, setBtResult] = useState<BacktestResponse | null>(null)
  const [openCode, setOpenCode] = useState<string | null>(null)
  const [btLabel, setBtLabel] = useState('')
  // 전 구간 검사 — 언제부터 언제까지, 그리고 지금 어디까지 왔나
  const [allStart, setAllStart] = useState(ALL_START_DEFAULT)
  const [allEnd, setAllEnd] = useState(todayStr)
  const [jobId, setJobId] = useState<string | null>(null)
  const [progress, setProgress] = useState<{ phase: string; done: number; total: number } | null>(null)

  /** 요청 본문 — 구간 검사와 전 구간 검사가 **같은 전략 값**을 보낸다.
   *
   *  검사 대상 종목은 **고른 전략에 붙어 있는 검색식**이다. 여기서 따로 고르지 않는다 —
   *  전략을 고르고 검색식을 또 고르면 "내가 고른 전략"과 "실제로 돈 값"이 어긋난다
   *  (오너 2026-08-10: "왜 검색식을 선택하는 거야? 전략을 선택할 수 있어야지"). */
  function buildRequest(): { req: BacktestRequest; filled: string[] } | null {
    if (draft.conditions.length === 0) {
      setBtMsg('이 전략에 검색식이 안 붙어 있습니다 — ②에서 검색식을 붙이세요.')
      return null
    }
    const filled: string[] = []
    let buy = draft.buy.filter((b) => b.enabled && b.ratio > 0 && b.ratio < 1)
    if (buy.length === 0) {
      buy = SIM_EXAMPLE.buy.map((b) => newBuyStage(b.ratio, b.weight))
      filled.push('분할 매수 3차(38.2/50/61.8%)')
    }
    // 파동·지지저항은 전략 1호 고정 정의 — 화면 입력 없음 (오너 결정 2026-08-06)
    const buyOff = Number(draft.buyTickOffset || '0')
    const sellOff = Number(draft.sellTickOffset || '0')
    const minGap = Number(draft.buyMinGapPct || '0')
    return {
      filled,
      req: {
        // 전 구간은 split 을 안 쓰지만 요청 형식은 하나로 유지한다(서버가 무시).
        split: btSplit === 'all' ? 'train' : btSplit,
        conditions: draft.conditions,
        logic: draft.logic,
        ...START_PAYLOAD,
        ...ZZ_PAYLOAD,
        ...BAND_PAYLOAD,
        ...SR_PAYLOAD,
        buy: buy.map((b) => ({ id: b.id, ratio: b.ratio, weight: b.weight, enabled: b.enabled })),
        sell: draft.sell.map((s) => ({
          id: s.id, rebound_pct: s.reboundPct, weight: s.weight, enabled: s.enabled,
        })),
        sell_basis: draft.sellBasis,
        buy_tick_offset: Number.isInteger(buyOff) ? buyOff : 0,
        sell_tick_offset: Number.isInteger(sellOff) ? sellOff : 0,
        buy_min_gap_pct: Number.isFinite(minGap) && minGap >= 0 ? minGap : 0,
        stop: stopPayload(draft),
        reenter_same_wave: draft.reenterSameWave,
        i_know_test_is_once: btSplit === 'test' ? btConfirmTest : undefined,
        screen_name: draft.screenName,
        label: btLabel.trim() || name,
      },
    }
  }

  async function runBacktest() {
    if (btSplit === 'test' && !btConfirmTest) {
      setBtMsg('Test 구간은 단 1회만 씁니다(§4.1) — 최종 평가가 맞다면 체크 후 실행하세요.')
      return
    }
    const built = buildRequest()
    if (!built) return

    // 전 구간은 몇 분 걸린다 — 시작만 하고 진행은 따로 확인한다(아래 폴링 효과).
    if (btSplit === 'all') {
      if (allStart >= allEnd) {
        setBtMsg('시작일이 종료일보다 앞서야 합니다.')
        return
      }
      setBtRunning(true)
      setBtResult(null)
      setProgress(null)
      setBtMsg('검사를 시작했습니다 — 몇 분 걸립니다. 이 탭을 떠나도 계속 돕니다.')
      try {
        const { job_id } = await postBacktestAll({ ...built.req, start: allStart, end: allEnd })
        setJobId(job_id)
      } catch (e) {
        setBtRunning(false)
        setBtMsg(e instanceof Error ? e.message : '전 구간 검사 시작 실패')
      }
      return
    }

    setBtRunning(true)
    setBtMsg('검사 중… (종목 수에 따라 수십 초)')
    try {
      const res = await postBacktest(built.req)
      setBtResult(res)
      setOpenCode(null)
      setLoadedFrom(null)
      setPage(1)
      void refreshRuns() // 방금 돌린 게 보관함 목록에 바로 보이게
      setBtMsg(built.filled.length ? `예시값 사용: ${built.filled.join(' · ')}` : '')
    } catch (e) {
      setBtResult(null)
      setBtMsg(e instanceof Error ? e.message : '백테스트 실패')
    } finally {
      setBtRunning(false)
    }
  }

  // 전 구간 검사 진행 확인 — 2초마다. 탭을 떠나도 서버는 계속 돌기 때문에, 돌아오면
  // 그대로 이어서 보인다(job_id 가 남아 있다).
  const jobRef = useRef<string | null>(null)
  useEffect(() => {
    jobRef.current = jobId
    if (!jobId) return
    let alive = true
    const tick = async () => {
      if (!alive || jobRef.current !== jobId) return
      try {
        const s = await fetchBacktestAll(jobId)
        if (!alive) return
        setProgress({ phase: s.phase, done: s.done, total: s.total })
        if (s.status === 'done' && s.result) {
          setBtResult(s.result)
          setOpenCode(null)
          setLoadedFrom(null)
          setPage(1)
          void refreshRuns()
          setBtRunning(false)
          setJobId(null)
          setBtMsg('')
        } else if (s.status === 'error') {
          setBtRunning(false)
          setJobId(null)
          setBtMsg(s.detail ?? '전 구간 검사 실패')
        }
      } catch (e) {
        if (!alive) return
        setBtRunning(false)
        setJobId(null)
        setBtMsg(e instanceof Error ? e.message : '진행 상황을 확인하지 못했습니다')
      }
    }
    const id = window.setInterval(() => void tick(), 2000)
    void tick()
    return () => {
      alive = false
      window.clearInterval(id)
    }
  }, [jobId])

  // 거르고 정렬한 뒤 한 쪽만 잘라 낸다 — **자르는 건 화면 표시일 뿐** 결과는 다 들고 있다.
  const shown = (() => {
    const needle = q.trim().toLowerCase()
    const all = sortRows(
      (btResult?.results ?? []).filter((r) => matches(r, only, needle)),
      sortBy,
    )
    const pages = Math.max(1, Math.ceil(all.length / perPage))
    const p = Math.min(page, pages)
    return { rows: all.slice((p - 1) * perPage, p * perPage), total: all.length, pages, page: p }
  })()

  // 처음 산 달로 묶은 성적. 서버 `run_store.by_month` 와 같은 정의를 화면에서도 쓴다 —
  // 보관함을 꺼내 보지 않고 바로 눈에 보여야 한다(오너 2026-08-09).
  const monthly = (() => {
    const by = new Map<string, number[]>()
    for (const r of btResult?.results ?? []) {
      if (!r.first_fill || r.net_return == null) continue
      const k = r.first_fill.slice(0, 7)
      by.set(k, [...(by.get(k) ?? []), r.net_return])
    }
    return [...by.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([month, xs]) => ({
        month,
        n: xs.length,
        win: xs.filter((x) => x > 0).length / xs.length,
        avg: xs.reduce((a, b) => a + b, 0) / xs.length,
        worst: Math.min(...xs),
        best: Math.max(...xs),
      }))
  })()

  if (!active) return null

  return (
    <div className="panel-body">
      {catErrNode}

      {/* ─────────────── ④ 백테스팅 — 전수 검사 (오너: "4번째로 백테스팅 탭") ─────────────── */}
      <Card title="백테스팅" sub="②에서 만든 전략 하나를 골라 전수 검사 — 수수료 포함, 고른 날까지의 데이터만 보고 값을 정한다">
        {/* ③ 시뮬레이션과 같은 방식으로 고른다 — 전략 하나에 검색식·분할·손절이 다 들어
            있으므로 여기서 검색식을 또 고르지 않는다(오너 2026-08-10). */}
        <KV label="전략">
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
            <option value="">
              지금 편집 중인 값 {Object.keys(saved).length === 0 ? '(저장된 전략 없음)' : ''}
            </option>
            {Object.keys(saved).map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
          <button type="button" onClick={onGoStrategy}>전략 편집</button>
        </KV>
        {/* 이 전략이 무엇을 검사하는지 — 검색식이 안 붙어 있으면 검사할 종목이 없다. */}
        {draft.conditions.length > 0 ? (
          <p className="hint">
            검사할 종목: <b>{draft.screenName || '이름 없는 검색식'}</b> (조건{' '}
            {draft.conditions.length}개, {draft.logic === 'and' ? '모두 만족' : '하나라도 만족'}) ·
            분할 매수 {draft.buy.filter((b) => b.enabled).length}차 · 매도{' '}
            {draft.sell.filter((s) => s.enabled).length}차 ·{' '}
            {draft.stopEnabled ? '손절 있음' : '손절 없음'}
          </p>
        ) : (
          <p className="hint warn">
            이 전략에 검색식이 안 붙어 있습니다 — 검사할 종목을 정할 수 없습니다.{' '}
            <button type="button" onClick={onGoStrategy}>②에서 붙이기</button>
          </p>
        )}
        <KV label="구간">
          <span className="radios" style={{ marginLeft: 'auto' }}>
            {(Object.keys(SPLIT_LABEL) as SplitKey[]).map((k) => (
              <label key={k}>
                <input type="radio" checked={btSplit === k} onChange={() => { setBtSplit(k); setBtConfirmTest(false) }} />
                {SPLIT_LABEL[k]}
              </label>
            ))}
          </span>
        </KV>
        {/* 아래 세 개가 무슨 소린지 화면에 없어서 오너가 "이거 뭔 소리야"라고 물었다
            (2026-08-10). 왜 기간을 갈라 놨는지를 한 문단으로 적는다. */}
        {btSplit === 'all' ? (
          <>
            <KV label="검사 기간">
              <input type="date" value={allStart} onChange={(e) => setAllStart(e.target.value)} />
              <span className="unit">~</span>
              <input type="date" value={allEnd} onChange={(e) => setAllEnd(e.target.value)} />
            </KV>
            <p className="hint">
              거래일마다 검색식을 다시 돌려, 그날 걸린 종목을 그날 기준으로 사고팝니다.
              <b> 아래 세 기간과는 아무 상관이 없습니다</b> — 여기 적은 날짜만 씁니다.
              같은 종목이라도 파동이 바뀌면 다시 걸고, 매매 중이면 새로 시작하지 않습니다.
              돈은 무한이라 동시 보유 제한이 없습니다 — 이 숫자는 계좌 수익률이 아니라
              "한 종목에 들어갔을 때 평균 어땠나"입니다. 몇 분 걸립니다.
            </p>
          </>
        ) : (
          <p className="hint">
            기간을 셋으로 갈라 둔 이유: 값을 이리저리 바꿔가며 제일 좋은 숫자가 나올 때까지
            맞추면 <b>그 기간에만 맞는 답</b>이 됩니다. 그래서 값 맞추는 건 첫 기간에서만 하고,
            둘째 기간에서 그 값이 딴 데서도 되는지 봅니다. 셋째 기간은 다 정한 뒤{' '}
            <b>딱 한 번</b> 채점하는 자리입니다 — 보고 고치면 채점이 아니게 됩니다.
          </p>
        )}
        {btSplit === 'test' && (
          <p className="hint warn">
            <label>
              <input type="checkbox" checked={btConfirmTest} onChange={(e) => setBtConfirmTest(e.target.checked)} />{' '}
              Test 구간은 <b>단 1회</b>만 씁니다(§4.1). 보고 고치면 Train이 됩니다 — 최종 평가가 맞습니다.
            </label>
          </p>
        )}
        <KV label="이 검사에 붙일 이름">
          <input
            style={{ flex: 1 }}
            placeholder={name || '예) 최소 간격 10% · 폭 3%'}
            value={btLabel}
            onChange={(e) => setBtLabel(e.target.value)}
          />
        </KV>
        <p className="hint">
          결과는 자동으로 보관됩니다 — 나중에 어느 달 성적이 나빴는지 잘라 볼 수 있습니다.
          이름을 붙여 두면 어떤 설정이었는지 찾기 쉽습니다.
        </p>
        <p className="hint">
          검색식·분할·손절 전부 <b>고른 전략에 들어 있는 값</b>을 그대로 씁니다. 값을 바꾸려면
          ②에서 고치세요 — 저장하지 않고 바꾼 값으로 돌려보려면 전략을 "지금 편집 중인 값"으로
          두면 됩니다. 수수료·세금은 왕복 정액률(placeholder), 지정가라 슬리피지 미적용.
        </p>
        <div className="form-row" style={{ marginTop: 8 }}>
          <button type="button" className="primary" style={{ flex: 1 }} disabled={btRunning} onClick={() => void runBacktest()}>
            {btRunning ? '전수 검사 중…' : '백테스트 실행'}
          </button>
        </div>
        {/* 몇 분짜리라 "돌고는 있나"가 보여야 한다 — 게이지 + 갯수 (오너 2026-08-10).
            단계가 둘(종목 고르기 → 매매 검사)이라 단계마다 0부터 다시 찬다. */}
        {btRunning && btSplit === 'all' && (
          <div className="bt-gauge">
            <div className="bt-gauge-head">
              <span>{progress?.phase ?? '시작하는 중'}</span>
              <span className="mono">
                {progress && progress.total > 0
                  ? `${progress.done.toLocaleString()} / ${progress.total.toLocaleString()}`
                  : '준비 중…'}
              </span>
            </div>
            <div className="bt-gauge-track">
              <div
                className="bt-gauge-fill"
                style={{
                  width:
                    progress && progress.total > 0
                      ? `${Math.min(100, (progress.done / progress.total) * 100)}%`
                      : '0%',
                }}
              />
            </div>
          </div>
        )}
        <MsgLine text={btMsg} warn={!!btMsg} />
      </Card>

      {/* 보관함 — 돌린 결과는 자동으로 담긴다. 여기가 꺼내 보는 자리다
          (오너 2026-08-10: "저장할 수 있으면 뭐하냐 불러오지를 못하는데"). */}
      <Card title="지난 검사 불러오기" sub={`보관함 ${runs.length}건 — 다시 돌리지 않고 결과만 꺼내 본다`}>
        <KV label="보관함">
          <select style={{ flex: 1 }} value={runId} onChange={(e) => setRunId(e.target.value)}>
            <option value="">불러올 검사 선택…</option>
            {runs.map((r) => (
              <option key={r.id} value={String(r.id)}>
                {r.label || `${r.id}번`} · {r.ran_at.slice(0, 16).replace('T', ' ')} ·{' '}
                {r.split === 'all' ? '전 기간' : r.split} {r.split_start}~{r.split_end} · 사고판{' '}
                {r.n_trades}건
                {r.win_rate != null ? ` · 이긴 비율 ${(r.win_rate * 100).toFixed(0)}%` : ''}
              </option>
            ))}
          </select>
          <button type="button" disabled={!runId} onClick={() => void loadRun(Number(runId))}>
            불러오기
          </button>
          <button type="button" disabled={!runId} onClick={() => void removeRun(Number(runId))}>
            삭제
          </button>
        </KV>
        {runs.length === 0 ? (
          <p className="hint">아직 담긴 게 없습니다 — 한 번 돌리면 자동으로 담깁니다.</p>
        ) : (
          <p className="hint">
            불러오면 아래 결과가 그때 것으로 바뀝니다. 표·거르기·차트 전부 방금 돌린 것과
            똑같이 씁니다. 다시 돌리지 않으니 몇 분 기다릴 필요가 없습니다.
          </p>
        )}
      </Card>

      {btResult && (
        <Card
          title={loadedFrom ? '결과 (보관함에서 꺼냄)' : '결과'}
          sub={
            loadedFrom
              ? `${loadedFrom} · ${btResult.split_start} ~ ${btResult.split_end}`
              : `${btResult.split_start} ~ ${btResult.split_end}`
          }
          flush
        >
          {/* 이 백테스트가 실제로 무엇을 했는지 한 문단으로. 전에는 "유니버스 24" 하나뿐이라
              오너가 "2019~2026 전 구간에서 24종목?"으로 읽었다(2026-08-09). */}
          <div className="sumcard">
            <p className="hint" style={{ margin: '0 0 8px' }}>
              {btResult.base_date ? (
                <>
                  <b>{btResult.base_date}</b> 하루의 시세로 검색식에 걸린{' '}
                  <b>{btResult.picked}종목</b>을 골라,{' '}
                  <b>{btResult.split_start} ~ {btResult.split_end}</b> 동안 종목당{' '}
                  <b>한 번씩</b> 사고팔아 봤습니다. 구간 도중에 종목을 다시 고르지는 않습니다.
                </>
              ) : (
                <>
                  <b>{btResult.split_start} ~ {btResult.split_end}</b> 의{' '}
                  <b>{btResult.trading_days?.toLocaleString()}거래일</b> 동안 날마다 검색식을
                  다시 돌려, 걸린 <b>{btResult.codes?.toLocaleString()}종목</b>을 걸린 날 기준으로
                  사고팔았습니다 (걸린 횟수 {btResult.screened_events?.toLocaleString()}번).
                  매매 중인 종목은 다 팔기 전까지 새로 시작하지 않습니다.
                </>
              )}
            </p>
            <div className="pills">
              <span>고른 종목 <b>{btResult.picked.toLocaleString()}</b></span>
              <span>사고판 {btResult.base_date ? '종목' : '횟수'} <b>{btResult.metrics.n_trades}</b></span>
              <span>한 주도 못 산 {btResult.base_date ? '종목' : '횟수'} <b>{btResult.no_fill}</b></span>
              <span>검사 못 한 종목 <b>{Object.keys(btResult.skipped).length}</b></span>
              {btResult.metrics.win_rate != null && (
                <span>이긴 비율 <b>{(btResult.metrics.win_rate * 100).toFixed(1)}%</b></span>
              )}
              {btResult.metrics.expectancy != null && (
                <span>종목당 평균 <b className={chgClass(btResult.metrics.expectancy)}>{(btResult.metrics.expectancy * 100).toFixed(2)}%</b></span>
              )}
              <span title="한 종목을 끝내고 그 돈으로 다음 종목을 사는 걸 22번 반복했다는 뜻입니다. 실제로는 여러 종목을 동시에 나눠 사므로 이 숫자는 참고만 하세요.">
                한 종목씩 이어서 굴렸다면{' '}
                <b className={chgClass(btResult.metrics.cum_net_return)}>{(btResult.metrics.cum_net_return * 100).toFixed(1)}%</b>
                <span className="dim"> (참고용)</span>
              </span>
            </div>
            {btResult.closed_metrics && (
              <p className="hint" style={{ margin: '8px 0 0' }}>
                구간이 끝날 때까지 <b>안 팔린 {btResult.open_rounds ?? 0}건</b>을 빼면 —
                사고판 {btResult.closed_metrics.n_trades}건, 이긴 비율{' '}
                <b>{((btResult.closed_metrics.win_rate ?? 0) * 100).toFixed(1)}%</b>, 평균{' '}
                <b className={chgClass(btResult.closed_metrics.expectancy ?? 0)}>
                  {((btResult.closed_metrics.expectancy ?? 0) * 100).toFixed(2)}%
                </b>
                . 오래 물려 있는 건이 통계에 섞이는 걸 구분해서 봅니다.
              </p>
            )}
          </div>
          {!btResult.metrics.reliable && (
            <p className="hint warn" style={{ padding: '0 16px' }}>
              사고판 게 {btResult.metrics.n_trades}건뿐입니다 — 30건이 안 되면 이 숫자를 믿지
              않습니다(가드레일).
            </p>
          )}
          {btResult.results.length > 0 && (
            <>
              {/* 잘라서 100줄만 보여주던 자리 — 이제 전부 볼 수 있다(거르기 + 쪽 나누기). */}
              <div className="bt-filters">
                <input
                  className="omni"
                  placeholder="종목명 · 코드로 찾기"
                  value={q}
                  onChange={(e) => { setQ(e.target.value); setPage(1) }}
                />
                <select value={only} onChange={(e) => { setOnly(e.target.value as RowFilter); setPage(1) }}>
                  {(Object.keys(FILTER_LABEL) as RowFilter[]).map((k) => (
                    <option key={k} value={k}>{FILTER_LABEL[k]}</option>
                  ))}
                </select>
                <select value={sortBy} onChange={(e) => { setSortBy(e.target.value as SortKey); setPage(1) }}>
                  {(Object.keys(SORT_LABEL) as SortKey[]).map((k) => (
                    <option key={k} value={k}>{SORT_LABEL[k]}</option>
                  ))}
                </select>
              </div>
              {shown.total === 0 ? (
                <p className="hint" style={{ padding: '0 16px 10px' }}>
                  거른 조건에 맞는 줄이 없습니다 — 조건을 넓혀 보세요.
                </p>
              ) : (
                <>
                  <table className="grid">
                    <thead>
                      <tr>
                        <th>종목</th>
                        <th className="num">산 횟수</th>
                        <th className="num">평단</th>
                        <th className="num">판 값</th>
                        <th className="num">수익률</th>
                        <th className="num">언제부터 언제까지</th>
                      </tr>
                    </thead>
                    <tbody>
                      {/* 전 구간 검사는 한 종목이 여러 번 나온다(라운드마다 한 줄) — 열쇠에
                          고른 날을 같이 넣어야 행이 겹치지 않는다. */}
                      {shown.rows.map((r) => (
                        <RowWithDetail
                          key={rowKey(r)}
                          row={r}
                          open={openCode === rowKey(r)}
                          onToggle={() => setOpenCode(openCode === rowKey(r) ? null : rowKey(r))}
                          planDate={btResult.base_date}
                          onChart={(row, date) => setChartFor({ row, date })}
                        />
                      ))}
                    </tbody>
                  </table>
                  <Pager
                    page={shown.page}
                    pages={shown.pages}
                    total={shown.total}
                    perPage={perPage}
                    onPage={setPage}
                    onPerPage={(n) => { setPerPage(n); setPage(1) }}
                  />
                </>
              )}
            </>
          )}

          {monthly.length > 1 && (
            <>
              <p className="hint" style={{ padding: '10px 16px 0' }}>
                <b>처음 산 달로 묶은 성적</b> — 유난히 나쁜 달이 있으면 그때 무슨 일이
                있었는지 따로 봐야 합니다.
              </p>
              <table className="grid tight">
                <thead>
                  <tr>
                    <th>산 달</th>
                    <th className="num">종목</th>
                    <th className="num">이긴 비율</th>
                    <th className="num">평균</th>
                    <th className="num">최악</th>
                    <th className="num">최고</th>
                  </tr>
                </thead>
                <tbody>
                  {monthly.map((m) => (
                    <tr key={m.month}>
                      <td>{m.month}</td>
                      <td className="num">{m.n}</td>
                      <td className="num">{(m.win * 100).toFixed(0)}%</td>
                      <td className={`num ${chgClass(m.avg)}`}>{(m.avg * 100).toFixed(1)}%</td>
                      <td className={`num ${chgClass(m.worst)}`}>{(m.worst * 100).toFixed(1)}%</td>
                      <td className={`num ${chgClass(m.best)}`}>{(m.best * 100).toFixed(1)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          {btResult.no_fill_rows.length > 0 && (
            <>
              <p className="hint" style={{ padding: '10px 16px 0' }}>
                <b>한 주도 못 산 종목 {btResult.no_fill_rows.length}개</b> — 걸어 둔 값까지
                가격이 안 내려왔습니다. 눌러서 얼마에 걸었는지 볼 수 있습니다.
              </p>
              <table className="grid">
                <tbody>
                  {btResult.no_fill_rows.slice(0, noFillShown).map((r) => (
                    <RowWithDetail
                      key={rowKey(r)}
                      row={r}
                      open={openCode === rowKey(r)}
                      onToggle={() => setOpenCode(openCode === rowKey(r) ? null : rowKey(r))}
                      planDate={btResult.base_date}
                      onChart={(row, date) => setChartFor({ row, date })}
                      compact
                    />
                  ))}
                </tbody>
              </table>
              {noFillShown < btResult.no_fill_rows.length && (
                <div className="bt-pager">
                  <span className="dim">
                    {noFillShown.toLocaleString()} / {btResult.no_fill_rows.length.toLocaleString()}줄
                  </span>
                  <button
                    type="button"
                    style={{ marginLeft: 'auto' }}
                    onClick={() => setNoFillShown((n) => n + 200)}
                  >
                    200줄 더 보기
                  </button>
                  <button type="button" onClick={() => setNoFillShown(btResult.no_fill_rows.length)}>
                    전부 보기
                  </button>
                </div>
              )}
            </>
          )}

          {Object.keys(btResult.skipped).length > 0 && (
            <p className="hint" style={{ padding: '10px 16px 0' }}>
              <b>검사 못 한 종목</b>:{' '}
              {Object.entries(btResult.skipped)
                .slice(0, 8)
                .map(([c, why]) => `${c}(${why})`)
                .join(' · ')}
              {Object.keys(btResult.skipped).length > 8 ? ' …' : ''}
            </p>
          )}

          <p className="hint" style={{ padding: '10px 16px 10px' }}>
            {btResult.base_date ? (
              <>
                지금 방식의 한계: 종목을 고르는 건 <b>{btResult.base_date} 하루뿐</b>이고, 종목당
                <b> 한 번</b>만 사고팝니다. 날마다 다시 고르려면 구간을 <b>전 구간</b>으로 바꾸세요.
              </>
            ) : (
              <>
                지금 방식의 한계: <b>돈이 무한</b>이라는 전제입니다 — 동시에 몇 종목까지 들지,
                한 종목에 얼마를 넣을지를 안 따집니다. 계좌 수익률로 읽으면 안 됩니다.
              </>
            )}{' '}
            구간이 끝날 때까지 안 팔린 물량은 마지막 종가로 계산합니다.
          </p>
        </Card>
      )}

      {/* 행에서 연 차트 — 이 화면 위에 뜬다. 열쇠에 종목·기준일을 넣어, 다른 행을 누르면
          새로 마운트되면서 그 종목으로 다시 그린다. */}
      {chartFor && (
        <RowChart
          key={`${chartFor.row.code}@${chartFor.date}`}
          row={chartFor.row}
          planDate={chartFor.date}
          draft={draft}
          onClose={() => setChartFor(null)}
        />
      )}
    </div>
  )
}
