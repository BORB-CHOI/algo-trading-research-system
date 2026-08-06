import { useState } from 'react'
import type { ReactNode } from 'react'
import { postBacktest, type BacktestResponse } from '../../../api'
import { chgClass, fmtPrice } from '../../format'
import { Card, KV, MsgLine } from '../../components/ui'
import { SR_PAYLOAD, STRATEGY_ONE_WAVE } from '../strategyOne'
import type { Screens } from '../screenStore'
import { newBuyStage, type StrategyDraft } from '../strategyStore'
import { SIM_EXAMPLE } from './common'

// ④ 백테스팅 — 전수 검사 (layer4 strategy_one). 전략 값은 ②의 현재 값(draft)을 쓴다.
// StrategyPanel.tsx 분할(구조 리팩토링 2026-08-06)로 옮겨온 스텝.

// ④ 구간 표시용 — 정본은 layer4 backtest.SPLITS (§4.1 3분할). 여기는 라벨만.
const SPLIT_LABEL = {
  train: 'Train 2020~2023 (실험)',
  validate: 'Validate 2024 (검증)',
  test: 'Test 2025 (단 1회)',
} as const
type SplitKey = keyof typeof SPLIT_LABEL

export function BacktestStep(props: {
  active: boolean
  catErrNode: ReactNode
  screens: Screens
  draft: StrategyDraft
}) {
  const { active, catErrNode, screens, draft } = props

  const [btSplit, setBtSplit] = useState<SplitKey>('train')
  const [btScreen, setBtScreen] = useState('')
  const [btConfirmTest, setBtConfirmTest] = useState(false)
  const [btRunning, setBtRunning] = useState(false)
  const [btMsg, setBtMsg] = useState('')
  const [btResult, setBtResult] = useState<BacktestResponse | null>(null)

  async function runBacktest() {
    const scr = screens[btScreen]
    if (!scr || scr.conditions.length === 0) {
      setBtMsg('①에서 만든 검색식을 고르세요 — 유니버스(종목 선정)가 백테스트의 시작입니다.')
      return
    }
    if (btSplit === 'test' && !btConfirmTest) {
      setBtMsg('Test 구간은 단 1회만 씁니다(§4.1) — 최종 평가가 맞다면 체크 후 실행하세요.')
      return
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
    setBtRunning(true)
    setBtMsg('전수 검사 중… (유니버스 크기에 따라 수십 초)')
    try {
      const res = await postBacktest({
        split: btSplit,
        conditions: scr.conditions,
        logic: scr.logic,
        cycle_drop_pct: STRATEGY_ONE_WAVE.cycleDropPct,
        ...SR_PAYLOAD,
        buy: buy.map((b) => ({ id: b.id, ratio: b.ratio, weight: b.weight, enabled: b.enabled })),
        sell: draft.sell.map((s) => ({
          id: s.id, rebound_pct: s.reboundPct, weight: s.weight, enabled: s.enabled,
        })),
        sell_basis: draft.sellBasis,
        buy_tick_offset: Number.isInteger(buyOff) ? buyOff : 0,
        sell_tick_offset: Number.isInteger(sellOff) ? sellOff : 0,
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
        i_know_test_is_once: btSplit === 'test' ? btConfirmTest : undefined,
      })
      setBtResult(res)
      setBtMsg(filled.length ? `예시값 사용: ${filled.join(' · ')}` : '')
    } catch (e) {
      setBtResult(null)
      setBtMsg(e instanceof Error ? e.message : '백테스트 실패')
    } finally {
      setBtRunning(false)
    }
  }

  if (!active) return null

  return (
    <div className="panel-body">
      {catErrNode}

      {/* ─────────────── ④ 백테스팅 — 전수 검사 (오너: "4번째로 백테스팅 탭") ─────────────── */}
      <Card title="백테스팅" sub="검색식 유니버스 전 종목에 전략 1호 — 비용 포함, 기준일 왼쪽만 보고 세팅">
        <KV label="검색식">
          <select style={{ flex: 1 }} value={btScreen} onChange={(e) => setBtScreen(e.target.value)}>
            <option value="">①에서 만든 검색식 선택…</option>
            {Object.keys(screens).map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        </KV>
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
        {btSplit === 'test' && (
          <p className="hint warn">
            <label>
              <input type="checkbox" checked={btConfirmTest} onChange={(e) => setBtConfirmTest(e.target.checked)} />{' '}
              Test 구간은 <b>단 1회</b>만 씁니다(§4.1). 보고 고치면 Train이 됩니다 — 최종 평가가 맞습니다.
            </label>
          </p>
        )}
        <p className="hint">
          전략 값은 ② 매매전략의 현재 값(분할·손절·기법 파라미터)을 그대로 씁니다.
          수수료·세금은 왕복 정액률(placeholder), 지정가라 슬리피지 미적용.
        </p>
        <div className="form-row" style={{ marginTop: 8 }}>
          <button className="primary" style={{ flex: 1 }} disabled={btRunning} onClick={() => void runBacktest()}>
            {btRunning ? '전수 검사 중…' : '백테스트 실행'}
          </button>
        </div>
        <MsgLine text={btMsg} warn={!!btMsg} />
      </Card>

      {btResult && (
        <Card title="결과" sub={`기준일 ${btResult.base_date} — ${SPLIT_LABEL[btResult.split as SplitKey] ?? btResult.split}`} flush>
          <div className="sumcard">
            <div className="pills">
              <span>유니버스 <b>{btResult.universe.toLocaleString()}</b></span>
              <span>거래 <b>{btResult.metrics.n_trades}</b></span>
              <span>미체결 <b>{btResult.no_fill}</b></span>
              <span>스킵 <b>{Object.keys(btResult.skipped).length}</b></span>
              {btResult.metrics.win_rate != null && (
                <span>승률 <b>{(btResult.metrics.win_rate * 100).toFixed(1)}%</b></span>
              )}
              {btResult.metrics.expectancy != null && (
                <span>평균 <b className={chgClass(btResult.metrics.expectancy)}>{(btResult.metrics.expectancy * 100).toFixed(2)}%</b></span>
              )}
              <span>누적(순차 복리) <b className={chgClass(btResult.metrics.cum_net_return)}>{(btResult.metrics.cum_net_return * 100).toFixed(1)}%</b></span>
            </div>
          </div>
          {!btResult.metrics.reliable && (
            <p className="hint warn" style={{ padding: '0 16px' }}>
              거래 {btResult.metrics.n_trades}건 — N&lt;30 은 통계를 신뢰하지 않습니다(가드레일).
            </p>
          )}
          {btResult.results.length > 0 && (
            <table className="grid">
              <thead>
                <tr>
                  <th>종목</th>
                  <th className="num">매수</th>
                  <th className="num">평단</th>
                  <th className="num">청산</th>
                  <th className="num">순수익률</th>
                  <th className="num">기간</th>
                </tr>
              </thead>
              <tbody>
                {btResult.results.slice(0, 100).map((r) => (
                  <tr key={r.code}>
                    <td>{r.code}{r.stopped ? ' 손절' : ''}</td>
                    <td className="num">{r.n_buys}차</td>
                    <td className="num">{r.avg_entry != null ? fmtPrice(r.avg_entry) : '-'}</td>
                    <td className="num">{r.exit_value != null ? fmtPrice(r.exit_value) : '-'}</td>
                    <td className={`num ${chgClass(r.net_return ?? 0)}`}>
                      {r.net_return != null ? `${(r.net_return * 100).toFixed(1)}%` : '-'}
                    </td>
                    <td className="num">{r.first_fill} ~ {r.last_exit}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {btResult.results.length > 100 && (
            <p className="hint" style={{ padding: '0 16px' }}>상위 100종목만 표시 — 전체 {btResult.results.length}종목.</p>
          )}
          <p className="hint" style={{ padding: '0 16px 10px' }}>
            유니버스 선별은 기준일 1회(v1) · 종목당 라운드 1회(재매수 루프는 엔진 확장 예정) ·
            잔여 포지션은 구간 마지막 종가 평가.
          </p>
        </Card>
      )}
    </div>
  )
}
