import type { BacktestResponse } from '../../../api'
import { chgClass } from '../../format'
import { byYear, distribution, headline } from './backtestRows'

// ④ 성적 — **표보다 먼저 보는 것**. 오너 2026-08-10 선택: "성적 먼저, 표는 아래".
//
// 규칙 (dataviz):
// - 한 줄짜리 숫자는 차트가 아니라 **타일**이다. 막대 하나짜리 그래프를 만들지 않는다.
// - 위/아래(플러스·마이너스)는 **양극 색**이다. 이 시장의 상승=빨강 / 하락=파랑을 쓴다
//   (팔레트 검증 통과: 보통 시야 ΔE 36.3, 색약 24.8).
// - 막대는 얇게, 값은 막대 끝에만. 모든 점에 숫자를 달지 않는다.
// - 눈금선은 실선 헤어라인. 점선은 "예상치"로 읽혀서 안 쓴다.
// - 색만으로 뜻을 전하지 않는다 — 값이 글자로 항상 같이 있다.

/** 큰 숫자 하나 + 딸린 설명. 지표 몇 개를 나란히 놓는 자리. */
function Tile(props: {
  readonly label: string
  readonly value: string
  readonly sub?: string
  readonly tone?: 'up' | 'down' | ''
  readonly title?: string
}) {
  return (
    <div className="bt-tile" title={props.title}>
      <span className="lb">{props.label}</span>
      <b className={`v ${props.tone ?? ''}`}>{props.value}</b>
      {props.sub && <span className="sb">{props.sub}</span>}
    </div>
  )
}

const BAR_H = 22 // 한 줄 높이
const BAR_MAX = 18 // 막대 두께 상한 — 칸을 꽉 채우지 않는다(남는 건 공기)

/** 0을 가운데 두고 좌우로 뻗는 막대. 해마다 산 게 어땠나 / 수익률이 어떻게 흩어졌나. */
function DivergingBars(props: {
  readonly rows: readonly { key: string; value: number; note: string }[]
  readonly format: (v: number) => string
}) {
  const { rows, format } = props
  const max = Math.max(...rows.map((r) => Math.abs(r.value)), 1e-9)
  const H = rows.length * BAR_H

  return (
    <svg className="bt-bars" viewBox={`0 0 100 ${H}`} preserveAspectRatio="none" role="img">
      {/* 0선 — 실선 헤어라인, 배경에서 한 단계만 진하게 */}
      <line x1="50" y1="0" x2="50" y2={H} stroke="var(--hts-border)" strokeWidth="0.3" />
      {rows.map((r, i) => {
        const w = (Math.abs(r.value) / max) * 46 // 좌우 각각 최대 46 (여백 4)
        const up = r.value >= 0
        const y = i * BAR_H + (BAR_H - BAR_MAX) / 2
        return (
          <rect
            key={r.key}
            x={up ? 50 : 50 - w}
            y={y}
            width={Math.max(w, 0.4)}
            height={BAR_MAX}
            rx="1"
            fill={up ? 'var(--hts-up)' : 'var(--hts-down)'}
          >
            <title>{`${r.key} — ${format(r.value)} (${r.note})`}</title>
          </rect>
        )
      })}
    </svg>
  )
}

/** 막대 옆에 붙는 글자 줄 — SVG 안에 글자를 넣으면 가로로 늘어나 찌그러진다. */
function BarRows(props: {
  readonly rows: readonly { key: string; value: number; note: string }[]
  readonly format: (v: number) => string
}) {
  return (
    <div className="bt-barlabels">
      {props.rows.map((r) => (
        <div key={r.key} className="row" style={{ height: BAR_H }}>
          <span className="k">{r.key}</span>
          <span className={`v ${chgClass(r.value)}`}>{props.format(r.value)}</span>
          <span className="n">{r.note}</span>
        </div>
      ))}
    </div>
  )
}

export function BacktestScore({ result }: { readonly result: BacktestResponse }) {
  const h = headline(result)
  const years = byYear(result.results)
  const bins = distribution(result.results)
  const binMax = Math.max(...bins.map((b) => b.n), 1)

  return (
    <div className="bt-score">
      {/* 이 검사가 무엇을 한 건지 한 줄 — 숫자를 읽기 전에 알아야 한다. */}
      <p className="bt-what">
        {h.isWalkForward ? (
          <>
            <b>{result.split_start} ~ {result.split_end}</b> 동안{' '}
            <b>{h.tradingDays?.toLocaleString()}거래일</b> 내내 날마다 종목을 다시 골라,{' '}
            <b>{h.codes?.toLocaleString()}종목</b>을 <b>{h.nTrades.toLocaleString()}번</b> 사고팔았습니다.
          </>
        ) : (
          <>
            <b>{result.base_date}</b> 하루에 걸린 <b>{result.picked}종목</b>을{' '}
            <b>{result.split_start} ~ {result.split_end}</b> 동안 종목당 한 번씩 사고팔았습니다.
          </>
        )}
      </p>

      <div className="bt-tiles">
        <Tile label="이긴 비율" value={h.winRate == null ? '-' : `${(h.winRate * 100).toFixed(1)}%`} sub={`${h.nTrades.toLocaleString()}번 중 ${Math.round((h.winRate ?? 0) * h.nTrades).toLocaleString()}번`} />
        <Tile
          label="한 번에 평균"
          value={h.expectancy == null ? '-' : `${h.expectancy > 0 ? '+' : ''}${(h.expectancy * 100).toFixed(2)}%`}
          tone={(h.expectancy ?? 0) >= 0 ? 'up' : 'down'}
          sub="수수료·세금 뺀 값"
          title="한 종목에 한 번 들어갔을 때 평균적으로 어땠나. 계좌 수익률이 아닙니다."
        />
        {h.closedTrades != null && (
          <Tile
            label="다 판 것만"
            value={h.closedExpectancy == null ? '-' : `${h.closedExpectancy > 0 ? '+' : ''}${(h.closedExpectancy * 100).toFixed(2)}%`}
            tone={(h.closedExpectancy ?? 0) >= 0 ? 'up' : 'down'}
            sub={`${h.closedTrades.toLocaleString()}번 · 이긴 비율 ${((h.closedWinRate ?? 0) * 100).toFixed(1)}%`}
            title="아직 안 판 건을 뺀 성적. 오래 물려 있는 게 통계에 섞이는 걸 구분해서 봅니다."
          />
        )}
        {h.openRounds > 0 && (
          <Tile
            label="아직 안 판 것"
            value={h.openRounds.toLocaleString()}
            sub="마지막 종가로 계산"
            title="구간이 끝날 때까지 목표가에 안 닿은 건. 강제로 판 걸로 치지 않습니다."
          />
        )}
      </div>

      {!h.reliable && (
        <p className="hint warn">
          사고판 게 {h.nTrades}번뿐입니다 — 30번이 안 되면 이 숫자를 믿지 않습니다(가드레일).
        </p>
      )}

      <div className="bt-panes">
        {years.length > 1 && (
          <section>
            <h5>어느 해에 산 게 좋았나</h5>
            <p className="hint">처음 산 날 기준. 유난히 나쁜 해가 있으면 그때 무슨 일이 있었는지 따로 봐야 합니다.</p>
            <div className="bt-chart2">
              <DivergingBars
                rows={years.map((y) => ({
                  key: y.year,
                  value: y.avgNet,
                  note: `${y.n.toLocaleString()}번 · 이긴 비율 ${(y.winRate * 100).toFixed(0)}%`,
                }))}
                format={(v) => `${v > 0 ? '+' : ''}${(v * 100).toFixed(1)}%`}
              />
              <BarRows
                rows={years.map((y) => ({
                  key: y.year,
                  value: y.avgNet,
                  note: `${y.n.toLocaleString()}번 · ${(y.winRate * 100).toFixed(0)}%`,
                }))}
                format={(v) => `${v > 0 ? '+' : ''}${(v * 100).toFixed(1)}%`}
              />
            </div>
          </section>
        )}

        <section>
          <h5>수익률이 어떻게 흩어졌나</h5>
          <p className="hint">평균 하나만 보면 몇 건의 대박에 가려집니다. 어느 쪽에 몰려 있는지를 봅니다.</p>
          <div className="bt-hist">
            {bins.map((b) => (
              <div key={b.label} className="row">
                <span className="k">{b.label}</span>
                <span className="track">
                  <span
                    className="fill"
                    style={{
                      width: `${(b.n / binMax) * 100}%`,
                      background: b.to <= 0 ? 'var(--hts-down)' : 'var(--hts-up)',
                    }}
                  />
                </span>
                <span className="n">{b.n.toLocaleString()}</span>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  )
}
