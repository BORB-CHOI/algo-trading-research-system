import { useMemo } from 'react'
import type { ConditionDef } from '../../../api'
import { SELL_BASIS_LABEL, summarizeCond } from './common'
import { STRATEGY_ONE_WAVE } from '../strategyOne'
import type { StrategyDraft } from '../strategyStore'

/** ④ 백테스팅에서 **지금 고른 전략에 뭐가 걸려 있는지** 펼쳐 보는 자리.
 *
 *  오너 2026-08-23: "백테스팅에서 현재 내가 고른 전략이 어떤 조건값들을 세팅해둔 건지도
 *  볼 수 있게 해." ②로 건너가지 않고 여기서 바로 확인한다. **보여주기 전용**이다 —
 *  여기서 고치면 값이 두 군데로 갈린다(고치는 자리는 ② 하나).
 */
export function StrategySummary(props: {
  readonly draft: StrategyDraft
  readonly condMap: Map<string, ConditionDef>
}) {
  const { draft: d, condMap } = props

  const newHighDays = useMemo(() => {
    const days = d.conditions
      .filter((c) => c.key === 'new_high' || c.key === 'new_high_burst')
      .map((c) => Number(c.params?.days))
      .filter((n) => Number.isFinite(n) && n > 0)
    return days.length ? Math.max(...days) : null
  }, [d.conditions])

  const buys = d.buy.filter((b) => b.enabled)
  const sells = d.sell.filter((s) => s.enabled)
  const coolPct = Number(d.waveCoolPct || '0') || 0
  const gap = Number(d.buyMinGapPct || '0') || 0

  const stopText = !d.stopEnabled
    ? '안 겁니다'
    : d.stopMode === 'pct'
      ? `평단에서 ${d.stopPct || '?'}% 아래`
      : d.stopMode === 'fib'
        ? `되돌림 ${(Number(d.stopFibRatio) * 100).toFixed(1)}% 선에서 ${tick(d.stopTicks)}`
        : `${d.stopSource === 'custom' ? `${d.stopCustom || '?'}원` : '파동 바닥'}에서 ${tick(d.stopTicks)}`

  return (
    <details className="strat-sum">
      <summary>이 전략에 뭐가 걸려 있나</summary>

      <Row label="검사할 종목">
        <b>{d.screenName || '이름 없는 검색식'}</b>{' '}
        {d.conditions.length === 0 ? (
          <span className="warn">— 검색식이 안 붙어 있습니다</span>
        ) : (
          <span className="dim">
            · 조건 {d.conditions.length}개를 {d.logic === 'and' ? '모두' : '하나라도'} 만족
          </span>
        )}
        {d.conditions.length > 0 && (
          <ul>
            {d.conditions.map((c, i) => (
              <li key={`${c.key}-${i}`}>{summarizeCond(c, condMap.get(c.key))}</li>
            ))}
          </ul>
        )}
      </Row>

      <Row label="되돌림을 어디서 재나">
        {newHighDays == null
          ? '파동 바닥부터 그 뒤 가장 높았던 고가까지'
          : `최근 ${newHighDays}거래일 중 가장 높았던 고가까지`}
      </Row>

      <Row label="매수를 언제까지 기다리나">{d.buyWaitDays || '365'}일</Row>

      <Row label="거래 끊긴 파동 제외">
        {coolPct > 0 ? `거래가 한창때의 ${coolPct}%까지 줄면 뺍니다` : '안 씁니다 (파동을 전부 봅니다)'}
      </Row>

      <Row label="분할 매수">
        {buys.length === 0 ? (
          <span className="warn">차수가 하나도 안 켜져 있습니다</span>
        ) : (
          <>
            {buys.map((b, i) => (
              <span key={b.id} className="chip">
                {i + 1}차 되돌림 {(b.ratio * 100).toFixed(1)}% · 비중 {b.weight}%
              </span>
            ))}
            <div className="dim">
              차수끼리 적어도 {gap}% 는 벌어지게 · 걸어 둔 선에서 {tick(d.buyTickOffset)}
            </div>
          </>
        )}
      </Row>

      <Row label="분할 매도">
        {sells.length === 0 ? (
          <span className="warn">차수가 하나도 안 켜져 있습니다</span>
        ) : (
          <>
            {sells.map((s, i) => (
              <span key={s.id} className="chip">
                {i + 1}차 반등 {s.reboundPct}% · 비중 {s.weight}%
              </span>
            ))}
            <div className="dim">
              {SELL_BASIS_LABEL[d.sellBasis]} 기준 · 걸어 둔 선에서 {tick(d.sellTickOffset)}
            </div>
          </>
        )}
      </Row>

      <Row label="손절">{stopText}</Row>

      <Row label="파동·지지저항 (전략 1호 고정)">
        <span className="dim">
          꼭대기·바닥은 좌우 {STRATEGY_ONE_WAVE.zzDepth / 2}봉을 보고 · 한 파동으로 치려면 적어도{' '}
          {STRATEGY_ONE_WAVE.zzDeviation}배는 움직여야 · 평평한 구간{' '}
          {STRATEGY_ONE_WAVE.startBoxBars}봉을 거래대금 {STRATEGY_ONE_WAVE.startVolumeMult}배로
          뚫은 날이 바닥 · 지지저항은 {STRATEGY_ONE_WAVE.srScope}에서 {STRATEGY_ONE_WAVE.srChannelWidthPct}%
          씩 묶어서 찾음
        </span>
      </Row>

      <p className="hint">고치는 자리는 ② 한 곳입니다. 여기서는 보기만 합니다.</p>
    </details>
  )
}

function Row(props: { readonly label: string; readonly children: React.ReactNode }) {
  return (
    <div className="strat-sum-row">
      <span className="k">{props.label}</span>
      <span className="v">{props.children}</span>
    </div>
  )
}

/** 호가 오프셋을 말로 — 0이면 "그 자리 그대로". */
function tick(v: string): string {
  const n = Number(v || '0') || 0
  if (n === 0) return '그 자리 그대로'
  return n > 0 ? `${n}호가 올려서` : `${-n}호가 내려서`
}
