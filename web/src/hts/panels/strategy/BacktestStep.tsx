import { useEffect, useRef, useState } from 'react'
import type { Dispatch, ReactNode, SetStateAction } from 'react'
import {
  deleteRun,
  fetchBacktestAll,
  fetchRunResult,
  fetchRuns,
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
import { BacktestScore } from './BacktestScore'
import { BacktestTable } from './BacktestTable'

// ④ 백테스팅 — 전수 검사 (layer4 strategy_one). 전략 값은 ②의 현재 값(draft)을 쓴다.
// StrategyPanel.tsx 분할(구조 리팩토링 2026-08-06)로 옮겨온 스텝.

// ④ 검사 구간 — **오너가 날짜로 정한다.** 코드가 기간을 갈라 놓지 않는다(ADR-0019).
//
// 2026-08-16 이전에는 여기 '값 맞추기용 / 확인용 / 채점용' 3분할 라디오가 있었다.
// 오너 결정으로 없앴다: "2007~ 나누지 않고 전체". 최종 확인은 나무 모의투자(단계 5)가 한다.
// 검사는 늘 **거래일마다 종목을 다시 고르는** 방식이다(layer4 walk_forward,
// 오너 2026-08-10: "그때부터 하루씩 지금까지 매매 가능해야지").

// 기본 시작일 — 리먼 사태(2008-09-15) 전부터 보겠다는 오너 지시.
// 서버 정본은 layer4 backtest.DEFAULT_START. 끝은 오늘(todayStr).
const ALL_START_DEFAULT = '2007-01-01'

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
  /** 검사 종료일 — 체결을 어디까지 볼지. 기준일에서 자르면 정작 이 행이 사고판 게
   *  전부 화면 밖으로 사라진다(오너 2026-08-17: "기준일 이후에 매매가 하나도 없어"). */
  readonly endDate: string
  readonly draft: StrategyDraft
  readonly onClose: () => void
}) {
  const { row: r, planDate, endDate, draft, onClose } = props
  const proRef = useRef<ProChartHandle>(null)
  const [msg, setMsg] = useState('그리는 중…')
  const [sim, setSim] = useState<SimulateResponse | null>(null)
  // 차트 데이터가 들어오기 전에 그리면 아무것도 안 보인다 — 둘 다 준비됐을 때만 그린다.
  const readyRef = useRef(false)
  const simRef = useRef<SimulateResponse | null>(null)
  const drawnRef = useRef(false)
  // 무엇을 보여줄 범위인가. 기본은 **이 매매 구간** — 행을 눌러 연 차트인데 전 기간을
  // 펼쳐 놓으면 정작 이 매매가 화면에서 점 하나가 된다(오너 2026-08-18:
  // "매매 기록 클릭하면 차트가 해당 구간으로 화면 포커싱 되야지").
  const [range, setRange] = useState<'trade' | 'all' | 'plan'>('trade')

  /** 이 매매가 벌어진 구간의 끝 — 마지막 청산. 안 팔렸으면 검사 끝까지. */
  const tradeEnd = r.last_exit ?? (r.open ? endDate : (r.first_fill ?? planDate))

  /** 기준일이 화면에 들어오도록 과거 데이터를 끌어온 뒤 범위를 맞춘다.
   *  `showUntil` 이 그 날짜까지 안 받아온 구간을 다시 받아 온다 — 이걸 먼저 해야
   *  '전 기간'에서 파동 바닥(수년 전일 수 있다)이 화면에 들어온다. */
  async function fit(mode: 'trade' | 'all' | 'plan') {
    if (mode === 'all') {
      // 먼저 기준일까지 끌어온 **뒤에** 전체로 편다 — 순서가 바뀌면 다시 그 날짜로
      // 스크롤돼서 뒤쪽(언제 샀고 팔았나)이 화면 밖에 남는다.
      await proRef.current?.showUntil(planDate)
      proRef.current?.setVisibleBars(0) // 0 = 받아온 봉 전부
      return
    }
    if (mode === 'trade') {
      // 기준일 ~ 마지막 청산만 화면에 담는다. 오른쪽 끝이 청산일이라 "언제 사서
      // 언제 팔았나"가 화면 한가운데 온다.
      await proRef.current?.showSpan(planDate, tradeEnd)
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
      // 계획은 **기준일 하루**, 체결은 그 다음날부터 검사 끝까지 — ④ 표의 이 한 줄과
      // 정확히 같은 매매 한 건만 그린다. 전에는 end=기준일 이라 데이터가 거기서 잘려,
      // 상관없는 옛 라운드 수십 건이 찍히고 정작 이 행의 체결은 하나도 안 보였다.
      plan_date: planDate,
      end: endDate,
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
        {/* 도구 막대는 ③ 시뮬레이션·차트 탭과 **같은 것**을 쓴다 — 봉 수(200·300·500·
            1000·전부)·주기·지표·그리기 도구가 다 여기 있다. 예전엔 숨기고 라디오 두 개만
            뒀는데, 정작 500봉으로 보는 길이 없었다(오너 2026-08-18: "기껏 모듈화 했는데"). */}
        <ProChart
          ref={proRef}
          initialSymbol={{ code: r.code, name: r.name || r.code, market: '' }}
          // 처음부터 **전 이력**을 받는다. 기본(500봉)이면 오늘 기준 500거래일치만
          // 받아와서, 몇 년 전 매매를 열면 그 앞이 통째로 없다 — 데이터가 없는 게
          // 아니라 안 받아온 것인데 "캔들이 끊겼다"로 보인다(오너 지적 2026-08-18).
          // 이 창은 지나간 매매 하나를 들여다보는 자리라 처음부터 다 있어야 한다.
          initialBars={0}
          layerToggles
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
              checked={range === 'trade'}
              onChange={() => {
                setRange('trade')
                void fit('trade')
              }}
            />
            이 매매
          </label>
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
  // 표의 거르기·정렬·쪽 나누기는 BacktestTable 이 스스로 들고 있다 — 여기서는 안 만진다.
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

  const [btRunning, setBtRunning] = useState(false)
  const [btMsg, setBtMsg] = useState('')
  const [btResult, setBtResult] = useState<BacktestResponse | null>(null)
  const [btLabel, setBtLabel] = useState('')
  // 검사 구간 — 언제부터 언제까지, 그리고 지금 어디까지 왔나
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
        screen_name: draft.screenName,
        label: btLabel.trim() || name,
      },
    }
  }

  async function runBacktest() {
    const built = buildRequest()
    if (!built) return
    if (allStart >= allEnd) {
      setBtMsg('시작하는 날이 끝나는 날보다 앞서야 합니다.')
      return
    }
    // 몇 분 걸린다 — 시작만 하고 진행은 따로 확인한다(아래 폴링 효과).
    setBtRunning(true)
    setBtResult(null)
    setProgress(null)
    setBtMsg(
      built.filled.length
        ? `예시값 사용: ${built.filled.join(' · ')} — 검사를 시작했습니다.`
        : '검사를 시작했습니다 — 몇 분 걸립니다. 이 탭을 떠나도 계속 돕니다.',
    )
    try {
      const { job_id } = await postBacktestAll({ ...built.req, start: allStart, end: allEnd })
      setJobId(job_id)
    } catch (e) {
      setBtRunning(false)
      setBtMsg(e instanceof Error ? e.message : '검사 시작 실패')
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
          setLoadedFrom(null)
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
        {/* 구간은 **오너가 날짜로 정한다.** 예전엔 여기 '값 맞추기용/확인용/채점용' 라디오가
            있었으나 없앴다 — 오너 결정 2026-08-16 "2007~ 나누지 않고 전체". */}
        <KV label="검사 기간">
          <input type="date" value={allStart} onChange={(e) => setAllStart(e.target.value)} />
          <span className="unit">~</span>
          <input type="date" value={allEnd} onChange={(e) => setAllEnd(e.target.value)} />
        </KV>
        <p className="hint">
          거래일마다 검색식을 다시 돌려, 그날 걸린 종목을 그날 기준으로 사고팝니다.
          같은 종목이라도 파동이 바뀌면 다시 걸고, 매매 중이면 새로 시작하지 않습니다.
          돈은 무한이라 동시 보유 제한이 없습니다 — 이 숫자는 계좌 수익률이 아니라
          "한 종목에 들어갔을 때 평균 어땠나"입니다. 몇 분 걸립니다.
        </p>
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
        {btRunning && (
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
                {r.split_start}~{r.split_end} · 사고판{' '}
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
        <>
          {/* 성적을 먼저, 표는 아래 (오너 2026-08-10 선택). */}
          <Card
            title={loadedFrom ? '성적 (보관함에서 꺼냄)' : '성적'}
            sub={loadedFrom ?? `${btResult.split_start} ~ ${btResult.split_end}`}
          >
            <BacktestScore result={btResult} />
          </Card>

          <Card
            title="종목별로 뭘 어떻게 사고팔았나"
            sub="종목을 누르면 그 종목의 매매가, 매매를 누르면 그때 뭘 보고 걸었는지가 나온다"
            flush
          >
            <BacktestTable result={btResult} onChart={(row, date) => setChartFor({ row, date })} />
            {btResult.no_fill > 0 && (
              <p className="hint" style={{ padding: '0 16px 12px' }}>
                한 주도 못 산 <b>{btResult.no_fill.toLocaleString()}건</b>은 위 표에 없습니다 —
                걸어 둔 값까지 가격이 안 내려온 것들이라 매매로 세지 않습니다.
              </p>
            )}
            <p className="hint" style={{ padding: '0 16px 12px' }}>
              {btResult.base_date
                ? '종목을 고른 건 그 하루뿐이고 종목당 한 번만 사고팝니다. 날마다 다시 고르려면 구간을 전 기간으로 바꾸세요.'
                : '돈이 무한이라는 전제입니다 — 동시에 몇 종목까지 들지, 한 종목에 얼마를 넣을지를 안 따집니다. 계좌 수익률로 읽으면 안 됩니다.'}
              {' '}구간이 끝날 때까지 안 팔린 물량은 마지막 종가로 계산합니다.
            </p>
          </Card>
        </>
      )}

      {/* 행에서 연 차트 — 이 화면 위에 뜬다. 열쇠에 종목·기준일을 넣어, 다른 행을 누르면
          새로 마운트되면서 그 종목으로 다시 그린다. */}
      {chartFor && btResult && (
        <RowChart
          key={`${chartFor.row.code}@${chartFor.date}`}
          row={chartFor.row}
          planDate={chartFor.date}
          endDate={btResult.split_end}
          draft={draft}
          onClose={() => setChartFor(null)}
        />
      )}
    </div>
  )
}
