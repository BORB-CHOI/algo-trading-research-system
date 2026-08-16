import { useState } from 'react'
import type { BacktestResponse, BacktestRow } from '../../../api'
import { chgClass, fmtPrice } from '../../format'
import {
  FILTER_LABEL,
  FIRST_DIR,
  prepare,
  type CodeGroup,
  type RowFilter,
  type SortCol,
  type SortDir,
} from './backtestRows'

// ④ 결과 표 — **종목(1단) → 라운드(2단) → 근거(3단)**.
//
// 전에는 라운드를 그냥 수천 줄로 늘어놨다. 그래서 두산을 찾으면 두산으로 시작하는
// 종목들의 라운드가 뒤섞여 쏟아졌다 (오너 2026-08-10: "정렬 안되서 개판이고",
// "2중 그룹화라도 하든가").
//
// 이제 한 종목이 한 줄이다. 그 줄에 그 종목의 성적(매매 횟수·이긴 비율·평균)이 있고,
// 펴면 라운드가 시간 순으로, 라운드를 또 펴면 그때 뭘 보고 걸었는지가 나온다.
// 정렬은 **열 머리를 눌러서** 한다 — 드롭다운을 찾아 헤매지 않는다.

const PER_PAGE = [25, 50, 100, 200] as const

function pct(v: number | null | undefined, digits = 1): string {
  return v == null ? '-' : `${(v * 100).toFixed(digits)}%`
}

/** 누를 수 있는 열 머리. 지금 정렬 중인 열은 화살표로 표시한다. */
function Th(props: {
  readonly col: SortCol
  readonly label: string
  readonly cur: SortCol
  readonly dir: SortDir
  readonly onSort: (c: SortCol) => void
  readonly align?: 'num'
  readonly title?: string
}) {
  const { col, label, cur, dir, onSort, align, title } = props
  const on = cur === col
  return (
    <th className={align === 'num' ? 'num sortable' : 'sortable'} title={title}>
      <button type="button" className={on ? 'on' : ''} onClick={() => onSort(col)}>
        {label}
        <span className="arrow">{on ? (dir === 'asc' ? '▲' : '▼') : '⇅'}</span>
      </button>
    </th>
  )
}

/** 라운드 한 줄(2단) + 펴면 나오는 근거(3단). */
function RoundRow(props: {
  readonly row: BacktestRow
  readonly fallbackDate: string | null
  readonly open: boolean
  readonly onToggle: () => void
  readonly onChart: (row: BacktestRow, date: string) => void
}) {
  const { row: r, fallbackDate, open, onToggle, onChart } = props
  const planDate = r.plan_date ?? fallbackDate
  const span = (r.wave_high ?? 0) - (r.wave_low ?? 0)
  const placeOf = (px: number) => (span > 0 ? ((r.wave_high! - px) / span) * 100 : null)

  return (
    <>
      <tr className="round" onClick={onToggle}>
        <td className="ind">
          <span className="mono dim">{open ? '▾' : '▸'}</span> {planDate ?? '-'}
          {r.stopped && <span className="chip warn">손절</span>}
          {r.open && <span className="chip">안 팔림</span>}
        </td>
        <td className="num">{r.n_buys}번</td>
        <td className="num">{r.avg_entry != null ? fmtPrice(r.avg_entry) : '-'}</td>
        <td className="num">{r.exit_value != null ? fmtPrice(r.exit_value) : '-'}</td>
        <td className={`num ${chgClass(r.net_return ?? 0)}`}>{pct(r.net_return)}</td>
        <td className="num dim">
          {r.first_fill ?? '못 삼'}
          {r.last_exit ? ` ~ ${r.last_exit}` : ''}
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={6} className="bt-detail">
            {planDate && (
              <button
                type="button"
                className="primary"
                style={{ marginBottom: 8 }}
                onClick={(e) => {
                  e.stopPropagation()
                  onChart(r, planDate)
                }}
              >
                차트로 보기 ({planDate} 기준)
              </button>
            )}
            <div className="bt-detail-grid">
              <div>
                <h5>어디를 보고 걸었나</h5>
                <ul>
                  <li>
                    파동 <b>{fmtPrice(r.wave_low ?? 0)}</b>
                    <span className="dim"> ({r.wave_low_date})</span> → <b>{fmtPrice(r.wave_high ?? 0)}</b>
                  </li>
                  {r.buy_orders?.map((o) => (
                    <li key={`b${o.tranche}`}>
                      매수 {o.tranche}번째 · 되돌림 {pct(o.ratio)} → <b>{fmtPrice(o.price ?? 0)}</b>
                      {placeOf(o.price ?? 0) != null && (
                        <span className="dim"> (실제 {placeOf(o.price ?? 0)!.toFixed(1)}% 자리)</span>
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
                    <li>손절 → <b>{fmtPrice(r.stop_price)}</b></li>
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
                        {/* 0 = 저가가 목표가에 딱 닿기만 했다 → 실전에선 못 샀을 수 있다.
                            오너가 호가 오프셋을 조절하며 볼 재료(2026-08-16). */}
                        <th className="num" title="저가가 목표가보다 몇 호가 더 내려갔나. 0이면 딱 닿기만 한 것이라 실전에서는 못 샀을 수 있습니다.">
                          여유
                        </th>
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
                            <td
                              className={`num ${f.slack_ticks === 0 ? 'down' : 'dim'}`}
                              title={
                                f.slack_ticks == null
                                  ? ''
                                  : f.slack_ticks === 0
                                    ? '목표가에 딱 닿기만 했습니다 — 실전에서는 못 샀을 수 있습니다'
                                    : `목표가보다 ${f.slack_ticks}호가 더 내려갔습니다`
                              }
                            >
                              {f.slack_ticks == null ? '—' : `${f.slack_ticks}호가`}
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                ) : (
                  <p className="hint">
                    한 주도 안 걸렸습니다.
                    {r.low_in_span != null && (
                      <> 이 구간 최저가가 <b>{fmtPrice(r.low_in_span)}</b> 라 1번째 지정가{' '}
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

/** 종목 한 줄(1단) — 그 종목 전체 성적. 펴면 라운드가 나온다. */
function GroupRow(props: {
  readonly group: CodeGroup
  readonly open: boolean
  readonly onToggle: () => void
  readonly fallbackDate: string | null
  readonly openRound: string | null
  readonly onOpenRound: (key: string | null) => void
  readonly onChart: (row: BacktestRow, date: string) => void
}) {
  const { group: g, open, onToggle, fallbackDate, openRound, onOpenRound, onChart } = props
  return (
    <>
      <tr className={open ? 'group open' : 'group'} onClick={onToggle}>
        <td>
          <span className="mono dim">{open ? '▾' : '▸'}</span> <b>{g.name || g.code}</b>{' '}
          <span className="dim mono">{g.code}</span>
          {g.openCount > 0 && <span className="chip">안 팔림 {g.openCount}</span>}
          {g.stoppedCount > 0 && <span className="chip warn">손절 {g.stoppedCount}</span>}
        </td>
        <td className="num">{g.n}번</td>
        <td className="num">{pct(g.winRate, 0)}</td>
        <td className={`num ${chgClass(g.avgNet)}`}>{pct(g.avgNet)}</td>
        <td className={`num ${chgClass(g.bestNet)}`}>{pct(g.bestNet)}</td>
        <td className="num dim">{g.lastDate}</td>
      </tr>
      {open &&
        g.rounds.map((r) => {
          const key = `${r.code}@${r.plan_date ?? r.first_fill ?? ''}`
          return (
            <RoundRow
              key={key}
              row={r}
              fallbackDate={fallbackDate}
              open={openRound === key}
              onToggle={() => onOpenRound(openRound === key ? null : key)}
              onChart={onChart}
            />
          )
        })}
    </>
  )
}

export function BacktestTable(props: {
  readonly result: BacktestResponse
  readonly onChart: (row: BacktestRow, date: string) => void
}) {
  const { result, onChart } = props
  const [q, setQ] = useState('')
  const [filter, setFilter] = useState<RowFilter>('all')
  const [col, setCol] = useState<SortCol>('avgNet')
  const [dir, setDir] = useState<SortDir>('desc')
  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState<number>(25)
  const [openCode, setOpenCode] = useState<string | null>(null)
  const [openRound, setOpenRound] = useState<string | null>(null)

  /** 같은 열을 다시 누르면 방향만 뒤집는다 — 표에서 늘 그러듯이. */
  function sortBy(next: SortCol) {
    if (next === col) {
      setDir(dir === 'asc' ? 'desc' : 'asc')
    } else {
      setCol(next)
      setDir(FIRST_DIR[next])
    }
    setPage(1)
  }

  const groups = prepare(result.results, { q, filter, col, dir })
  const pages = Math.max(1, Math.ceil(groups.length / perPage))
  const p = Math.min(page, pages)
  const shown = groups.slice((p - 1) * perPage, p * perPage)
  const rounds = groups.reduce((n, g) => n + g.n, 0)

  return (
    <>
      <div className="bt-filters">
        <input
          className="omni"
          placeholder="종목명 · 코드로 찾기"
          value={q}
          onChange={(e) => {
            setQ(e.target.value)
            setPage(1)
          }}
        />
        <select
          value={filter}
          onChange={(e) => {
            setFilter(e.target.value as RowFilter)
            setPage(1)
          }}
        >
          {(Object.keys(FILTER_LABEL) as RowFilter[]).map((k) => (
            <option key={k} value={k}>{FILTER_LABEL[k]}</option>
          ))}
        </select>
        <span className="dim">
          종목 <b>{groups.length.toLocaleString()}</b> · 매매 <b>{rounds.toLocaleString()}</b>번
        </span>
      </div>

      {groups.length === 0 ? (
        <p className="hint" style={{ padding: '0 16px 12px' }}>
          찾는 조건에 맞는 종목이 없습니다.
        </p>
      ) : (
        <>
          <table className="grid bt-grid">
            <thead>
              <tr>
                <Th col="name" label="종목" cur={col} dir={dir} onSort={sortBy} />
                <Th col="n" label="매매" cur={col} dir={dir} onSort={sortBy} align="num"
                  title="이 종목을 몇 번 사고팔았나" />
                <Th col="winRate" label="이긴 비율" cur={col} dir={dir} onSort={sortBy} align="num" />
                <Th col="avgNet" label="평균 수익률" cur={col} dir={dir} onSort={sortBy} align="num"
                  title="이 종목 매매들의 평균" />
                <Th col="bestNet" label="가장 좋았던 것" cur={col} dir={dir} onSort={sortBy} align="num" />
                <Th col="lastDate" label="마지막 고른 날" cur={col} dir={dir} onSort={sortBy} align="num" />
              </tr>
            </thead>
            <tbody>
              {shown.map((g) => (
                <GroupRow
                  key={g.code}
                  group={g}
                  open={openCode === g.code}
                  onToggle={() => setOpenCode(openCode === g.code ? null : g.code)}
                  fallbackDate={result.base_date}
                  openRound={openRound}
                  onOpenRound={setOpenRound}
                  onChart={onChart}
                />
              ))}
            </tbody>
          </table>
          <div className="bt-pager">
            <span className="dim">
              {pages.toLocaleString()}쪽 중 {p.toLocaleString()}쪽
            </span>
            <span style={{ marginLeft: 'auto' }}>
              <button type="button" disabled={p <= 1} onClick={() => setPage(1)}>맨 앞</button>
              <button type="button" disabled={p <= 1} onClick={() => setPage(p - 1)}>이전</button>
              <button type="button" disabled={p >= pages} onClick={() => setPage(p + 1)}>다음</button>
              <button type="button" disabled={p >= pages} onClick={() => setPage(pages)}>맨 뒤</button>
            </span>
            <select
              value={perPage}
              onChange={(e) => {
                setPerPage(Number(e.target.value))
                setPage(1)
              }}
            >
              {PER_PAGE.map((n) => (
                <option key={n} value={n}>{n}종목씩</option>
              ))}
            </select>
          </div>
        </>
      )}
    </>
  )
}
