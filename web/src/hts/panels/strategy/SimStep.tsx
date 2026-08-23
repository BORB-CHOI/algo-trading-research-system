import { useEffect, useRef, useState } from 'react'
import type { Dispatch, ReactNode, SetStateAction } from 'react'
import { postSimulate, type DeviationMode, type SimulateResponse, type Symbol } from '../../../api'
import { ProChart, type BaseBar, type ProChartHandle } from '../../../ProChart'
import { allVisible, type OverlayVisibility } from '../../../simVisibility'
import { chgClass, fmtChg, fmtPrice } from '../../format'
import { Card, Chip, Chips, KV, MsgLine } from '../../components/ui'
import type { ComputedPrices } from '../SplitStages'
import { BAND_PAYLOAD, SR_PAYLOAD, START_PAYLOAD, STRATEGY_ONE_WAVE } from '../strategyOne'
import { newBuyStage, toDraft, type Strategies, type StrategyDraft } from '../strategyStore'
import { SIM_EXAMPLE, SIM_SYM, SimSymbolPicker, stopPayload, todayStr } from './common'

// ③ 시뮬레이션 — 고른 종목에 전략 1호(올라간 구간 피보나치+분할)를 돌려 전용 차트로 확인 (오너 지시:
// 종목 차트 오버레이 ❌, 이 탭에서 본다). 계산은 전부 파이썬(/api/simulate).
// StrategyPanel.tsx 분할(구조 리팩토링 2026-08-06)로 옮겨온 스텝.
//
// 종목은 **검색으로 아무 종목이나** 고른다 (오너 지시 2026-08-07: "내가 예시로 알려줬던
// 종목들 다 볼 수도 없네"). ① 에서 뭘 골랐는지와는 무관하다 — 이 화면은 전략 설계를 눈으로
// 확인하는 자리지 종목 검증 자리가 아니다. 실전 적용은 백테스트 러너(ADR-0007) 몫.
//
// 파동 입력 = 꺾임점을 찾는 두 값(좌우 봉수·잔파동 기준)뿐이다. 시작점은 그 꺾임점 위에서
// '이번 상승장이 시작된 지점'으로 정한다(ADR-0013 6차). 지어낸 세 값은 폐기했다.
// 빈 값은 예시값으로 채워서 실행이 절대 막히지 않게 한다 — 채운 값은 화면에 보인다.

// ③ 차트 요소별 표시 필터 — 겹칠 때 하나씩 끄고 본다 (오너 지시 2026-08-06).
// 매수·매도 주문가 가로선을 없앴으므로(오너 2026-08-22) 그 칸도 뺀다 — 산·판 자리는
// '사고판 자리'가 켜고 끈다(봉 아래 매수, 봉 위 매도 표식).
const SIM_LAYERS: readonly (readonly [keyof OverlayVisibility, string])[] = [
  ['anchor', '파동'],
  ['fib', '되돌림'],
  ['sr', '지지저항'],
  ['stop', '손절'],
  ['fills', '사고판 자리'],
] as const

// 결과 요약 문단(SimFoot)은 삭제했다 — 오너 2026-08-09: "잡설 없애라고. 그냥 차트 보면 알게."
// 파동 구간·매도 기준·지지저항 개수는 전부 차트에 그려져 있다. 숫자로 다시 읽을 것은
// 아래 '체결 내역' 표 하나면 충분하다.

export function SimStep(props: {
  active: boolean
  catErrNode: ReactNode
  saved: Strategies
  name: string
  setName: Dispatch<SetStateAction<string>>
  draft: StrategyDraft
  setDraft: Dispatch<SetStateAction<StrategyDraft>>
  setComputed: Dispatch<SetStateAction<ComputedPrices>>
}) {
  const { active, catErrNode, saved, name, setName, draft, setDraft, setComputed } = props
  const set = <K extends keyof StrategyDraft>(k: K, v: StrategyDraft[K]) =>
    setDraft((d) => ({ ...d, [k]: v }))

  const proRef = useRef<ProChartHandle>(null)
  const [sym, setSym] = useState<Symbol>({ ...SIM_SYM })
  // 파동 기준 — 트레이딩뷰 원본 기본값에서 시작해 오너가 차트 보며 돌려본다.
  const [depth, setDepth] = useState(String(STRATEGY_ONE_WAVE.zzDepth))
  const [deviation, setDeviation] = useState(String(STRATEGY_ONE_WAVE.zzDeviation))
  const [devMode, setDevMode] = useState<DeviationMode>(STRATEGY_ONE_WAVE.zzDeviationMode)
  // 기준일 = **차트 오른쪽 끝 봉** (오너 요청 2026-08-08). 차트를 왼쪽으로 밀면 그만큼
  // 과거 시점이 되고, 그 시점에 없던 데이터는 어차피 서버가 안 본다.
  // 손으로 고치면 반대로 차트가 그 날짜로 옮겨 간다 — 둘이 항상 같은 곳을 가리키게.
  // 주봉·월봉도 그대로 된다(봉 날짜 = 그 주/달 마지막 거래일). 분봉은 데이터가 생기면
  // ProChart.baseStampOf 한 곳만 고치면 시각까지 실린다.
  const [simDate, setSimDate] = useState(todayStr)
  // 마지막으로 오간 기준일. 차트→입력, 입력→차트가 서로를 되부르는 고리를 끊는다.
  const simDateRef = useRef(simDate)

  function onBaseBar(b: BaseBar) {
    if (b.time === simDateRef.current) return
    simDateRef.current = b.time
    setSimDate(b.time)
  }

  function editSimDate(v: string) {
    simDateRef.current = v
    setSimDate(v)
    void proRef.current?.showUntil(v) // 형식이 덜 갖춰진 값은 차트 쪽에서 무시한다
  }

  // 왼쪽 설정 패널 접기 — 차트를 넓게 볼 때 (오너 2026-08-09)
  const [sideOpen, setSideOpen] = useState(true)
  const [simMsg, setSimMsg] = useState('')
  const [simRunning, setSimRunning] = useState(false)
  const [simResult, setSimResult] = useState<SimulateResponse | null>(null)
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

  // ③ 재진입 시 직전 결과를 다시 그린다. 종목은 initialSymbol 로 마운트 때 이미 실려 있다 —
  // 마운트 직후 showSymbol 을 부르면 초기 로드와 경합한다(ProChart props 주석 참고).
  // (분할 전에는 step 을 deps 로 썼다 — 이 컴포넌트는 항상 마운트라 active 가 같은 신호다.)
  useEffect(() => {
    if (!active) return
    const r = simResultRef.current
    if (r) proRef.current?.applySimulation({ lines: r.lines, fills: r.fills, series: r.series })
    // 차트가 새로 마운트되면 필터는 전체 표시로 초기화된다 — 이전 선택을 다시 입힌다.
    proRef.current?.setOverlayVisibility(simVisRef.current)
  }, [active])

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

    // 파동·지지저항은 전략 1호 고정 정의 — 화면 입력 없음 (오너 결정 2026-08-06:
    // "시작점·고점은 자동으로 구하는 건데 입력값을 왜 내가 만지나")
    const buyOff = Number(draft.buyTickOffset || '0')
    const sellOff = Number(draft.sellTickOffset || '0')
    const minGap = Number(draft.buyMinGapPct || '0')

    const hasQty = Number(draft.qty) > 0
    const qty = hasQty ? Number(draft.qty) : SIM_EXAMPLE.qtyShares
    if (!hasQty) filled.push(`수량 ${SIM_EXAMPLE.qtyShares}주`)

    setSimRunning(true)
    setSimMsg('계산 중…')
    try {
      const res = await postSimulate({
        code: sym.code,
        end: simDate || undefined,
        zz_depth: Number(depth) || STRATEGY_ONE_WAVE.zzDepth,
        zz_deviation: Number(deviation) || STRATEGY_ONE_WAVE.zzDeviation,
        zz_deviation_mode: devMode,
        // 지지저항 값은 ③에서 안 만진다 — 전략 1호 고정 정의 그대로 나간다
        // (오너 2026-08-09). 돌려보는 자리는 차트 패널의 전략 값이다.
        ...START_PAYLOAD,
        start_cool_pct: Number(draft.waveCoolPct || '0') || 0,
        ...BAND_PAYLOAD,
        ...SR_PAYLOAD,
        buy: buy.map((b) => ({
          id: b.id, ratio: b.ratio, weight: b.weight, enabled: b.enabled, price_override: b.priceOverride,
        })),
        sell: draft.sell.map((s) => ({
          id: s.id, rebound_pct: s.reboundPct, weight: s.weight, enabled: s.enabled, price_override: s.priceOverride,
        })),
        sell_basis: draft.sellBasis,
      conditions: draft.conditions,
        buy_tick_offset: Number.isInteger(buyOff) ? buyOff : 0,
        sell_tick_offset: Number.isInteger(sellOff) ? sellOff : 0,
        buy_min_gap_pct: Number.isFinite(minGap) && minGap >= 0 ? minGap : 0,
        qty,
        qty_type: hasQty ? draft.qtyType : 'shares',
        stop: stopPayload(draft),
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

  if (!active) return null

  return (
    <div className="panel-body">
      {catErrNode}

      {/* ─────────────── ③ 시뮬레이션 ─────────────── */}
      <div className={sideOpen ? 'sim-split' : 'sim-split side-closed'}>
        <div className="sim-side">
          <Card title="시뮬레이션" sub={`${sym.name} — 전략 1호`}>
            {/* 입력 = 전략 선택 + 기준일 + 사이클 하락 기준. 빈 값은 예시로 채워 실행한다. */}
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
                <option value="">지금 편집 중인 값 {Object.keys(saved).length === 0 ? '(저장된 전략 없음)' : ''}</option>
                {Object.keys(saved).map((n) => (
                  <option key={n} value={n}>{n}</option>
                ))}
              </select>
            </KV>
            <SimSymbolPicker
              value={sym}
              onPick={(next) => {
                setSym(next)
                // 종목이 바뀌면 이전 종목의 오버레이는 무의미하다 — 지우고 다시 실행하게 한다.
                proRef.current?.applySimulation(null)
                setSimResult(null)
                setSimMsg('')
                proRef.current?.showSymbol(next.code, next.name, next.market)
              }}
            />
            <KV label="기준일">
              <input
                type="date"
                value={simDate}
                onChange={(e) => editSimDate(e.target.value)}
                title="차트 오른쪽 끝 봉의 날짜. 여기서 고치면 차트가 그 날짜로 옮겨 간다. 휴장일이면 직전 거래일."
              />
            </KV>
            {/* ③ 입력은 **파동 잡는 값 둘**뿐이다. 지지저항 값은 여기 없다
                (오너 2026-08-09: "시뮬레이션 화면에서 지지저항 관련된 커스텀은 다 지우고").
                설명문도 없앴다 — "잡설 부분 싹다 지우라고". */}
            <KV label="꼭대기·바닥은 좌우">
              <input className="amt" value={depth} onChange={(e) => setDepth(e.target.value)} />
              <span className="unit">봉을 보고</span>
            </KV>
            <KV label="작은 흔들림은 어떻게 거르나">
              <span className="radios" style={{ marginLeft: 'auto' }}>
                {(['자동', '고정'] as const).map((m) => (
                  <label key={m}>
                    <input type="radio" checked={devMode === m} onChange={() => setDevMode(m)} />
                    {m}
                  </label>
                ))}
              </span>
            </KV>
            <KV label="한 파동으로 치려면 적어도">
              <input className="amt" value={deviation} onChange={(e) => setDeviation(e.target.value)} />
              <span className="unit">{devMode === '자동' ? '배는 움직여야' : '% 는 움직여야'}</span>
            </KV>
            <div className="form-row" style={{ marginTop: 8 }}>
              <button className="primary" style={{ flex: 1 }} disabled={simRunning} onClick={() => void runSimulation()}>
                {simRunning ? '계산 중…' : '시뮬레이션 실행'}
              </button>
              <button className="ghost" onClick={() => { proRef.current?.applySimulation(null); setSimResult(null); setSimMsg('') }}>
                지우기
              </button>
            </div>
            <MsgLine text={simMsg} warn={!!simMsg} />
            {/* 요소별 표시 필터 — 겹칠 때 하나씩 끄고 본다. 줌은 유지된다. */}
            <Chips className="sim-layers">
              {SIM_LAYERS.map(([k, label]) => (
                <Chip
                  key={k}
                  on={simVis[k]}
                  title={simVis[k] ? '숨기기' : '표시'}
                  onClick={() => toggleLayer(k)}
                >
                  {label}
                </Chip>
              ))}
            </Chips>
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
        {/* 접기 손잡이 — 차트를 넓게 보고 싶을 때 왼쪽 패널을 통째로 접는다 (오너 2026-08-09).
            차트는 ResizeObserver 로 폭 변화를 스스로 따라간다. */}
        <button
          type="button"
          className="side-toggle"
          aria-expanded={sideOpen}
          title={sideOpen ? '설정 패널 접기' : '설정 패널 펼치기'}
          onClick={() => setSideOpen((v) => !v)}
        >
          <span aria-hidden>{sideOpen ? '‹' : '›'}</span>
        </button>
        {/* 차트는 패널 전체를 쓴다 — 필터 칩은 왼쪽 사이드로 옮겼다
            (오너 지시 2026-08-06: "전부 왼쪽 사이드 탭으로 넘겨, 차트 크기 키우자") */}
        <div className="sim-chart">
          <div className="sim-canvas">
            <ProChart ref={proRef} initialSymbol={SIM_SYM} onBaseBar={onBaseBar} />
          </div>
        </div>
      </div>
    </div>
  )
}
