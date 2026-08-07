import { useEffect, useRef, useState } from 'react'
import type { Dispatch, ReactNode, SetStateAction } from 'react'
import { postSimulate, type SimulateResponse } from '../../../api'
import { ProChart, type ProChartHandle } from '../../../ProChart'
import { allVisible, type OverlayVisibility } from '../../../simVisibility'
import { chgClass, fmtChg, fmtPrice } from '../../format'
import { Card, Chip, Chips, KV, MsgLine } from '../../components/ui'
import type { ComputedPrices } from '../SplitStages'
import { SR_PAYLOAD, STRATEGY_ONE_WAVE } from '../strategyOne'
import { newBuyStage, toDraft, type Strategies, type StrategyDraft } from '../strategyStore'
import { SELL_BASIS_LABEL, SIM_EXAMPLE, SIM_SYM, SIM_SYMS, todayStr } from './common'

// ③ 시뮬레이션 — 대표 종목에 전략 1호(상승장 사이클+분할)를 돌려 전용 차트로 확인 (오너 지시:
// 종목 차트 오버레이 ❌, 이 탭에서 본다). 계산은 전부 파이썬(/api/simulate).
// StrategyPanel.tsx 분할(구조 리팩토링 2026-08-06)로 옮겨온 스텝.
//
// 종목은 **대표 종목(삼성전자) 고정**이다 — ① 에서 뭘 골랐는지와 무관 (오너 지시 2026-08-05:
// "내가 설계한 전략이 어떻게 되는지만 보고 싶은 거"). 이 화면은 전략 설계를 눈으로
// 확인하는 자리지 종목 검증 자리가 아니다. 실전 적용은 백테스트 러너(ADR-0007) 몫.
//
// 파동 입력은 사이클 하락 기준 하나뿐이다 — "급등" 개념은 없다 (오너 확정 2026-08-06).
// 빈 값은 예시값으로 채워서 실행이 절대 막히지 않게 한다 — 채운 값은 화면에 보인다.

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

// ③ 결과 요약 — 구간·지지선·매도 기준·최종 손익을 한 줄씩. 왼쪽 사이드에 둔다
// (오너 지시 2026-08-06: 차트 위아래 띠를 전부 사이드로 넘기고 차트를 키운다).
// 이름은 "피보나치 구간" — '상승장'은 폐기했다(한 번도 -50% 를 안 맞은 종목은 시작점이
// 수십 년 전이 되어 상승장이라는 말과 안 맞는다, 오너 2026-08-06).
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
        <b>피보나치 구간</b> {r.cycle.low_date} {fmtPrice(r.cycle.low_price)} → {r.cycle.high_date}{' '}
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
        <b>지지저항</b> 여러 번 닿은 가격대(존) {srCount}개 — 강한 순 · 시작점{' '}
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
  const [sym, setSym] = useState<(typeof SIM_SYMS)[number]>(SIM_SYM)
  const [simDate, setSimDate] = useState(todayStr)
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

    const hasQty = Number(draft.qty) > 0
    const qty = hasQty ? Number(draft.qty) : SIM_EXAMPLE.qtyShares
    if (!hasQty) filled.push(`수량 ${SIM_EXAMPLE.qtyShares}주`)

    setSimRunning(true)
    setSimMsg('계산 중…')
    try {
      const res = await postSimulate({
        code: sym.code,
        end: simDate || undefined,
        cycle_drop_pct: STRATEGY_ONE_WAVE.cycleDropPct,
        ...SR_PAYLOAD,
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

  if (!active) return null

  return (
    <div className="panel-body">
      {catErrNode}

      {/* ─────────────── ③ 시뮬레이션 ─────────────── */}
      <div className="sim-split">
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
            <KV label="종목">
              <select
                value={sym.code}
                onChange={(e) => {
                  const next = SIM_SYMS.find((x) => x.code === e.target.value) ?? SIM_SYM
                  setSym(next)
                  // 종목이 바뀌면 이전 종목의 오버레이는 무의미하다 — 지우고 다시 실행하게 한다.
                  proRef.current?.applySimulation(null)
                  setSimResult(null)
                  setSimMsg('')
                  proRef.current?.showSymbol(next.code, next.name, next.market)
                }}
              >
                {SIM_SYMS.map((x) => (
                  <option key={x.code} value={x.code}>
                    {x.name} ({x.code})
                  </option>
                ))}
              </select>
            </KV>
            <KV label="기준일">
              <input
                type="date"
                value={simDate}
                onChange={(e) => setSimDate(e.target.value)}
                title="기본 = 오늘 (휴장일이면 직전 거래일 기준). 과거 날짜를 주면 그 시점을 재현한다."
              />
            </KV>
            <KV label="파동·지지저항">
              사이클 -{STRATEGY_ONE_WAVE.cycleDropPct}%
              · 지지저항 = 트레이딩뷰 표준 존 최대 {STRATEGY_ONE_WAVE.srMaxChannels}개
            </KV>
            <p className="hint">전략 1호 고정 정의 — 시작점·고점은 자동 탐지. 정의가 바뀌면 화면이 아니라 정의를 고친다.</p>
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
            {/* 결과 요약 — 옛 표(저점/고점/매도 기준가/체결 마커)는 이 요약과 같은 내용이라 지웠다. */}
            {simResult && <SimFoot r={simResult} sellBasis={draft.sellBasis} />}
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
        {/* 차트는 패널 전체를 쓴다 — 필터 칩·결과 요약은 왼쪽 사이드로 옮겼다
            (오너 지시 2026-08-06: "전부 왼쪽 사이드 탭으로 넘겨, 차트 크기 키우자") */}
        <div className="sim-chart">
          <div className="sim-canvas">
            <ProChart ref={proRef} initialSymbol={SIM_SYM} />
          </div>
        </div>
      </div>
    </div>
  )
}
