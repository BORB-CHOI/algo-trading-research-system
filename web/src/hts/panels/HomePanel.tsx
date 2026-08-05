import { useCallback, useEffect, useRef, useState } from 'react'
import {
  fetchIndexBoards,
  fetchMarket,
  fetchNews,
  fetchRanking,
  type IndexBoard,
  type MarketGroup,
  type NewsItem,
  type RankItem,
  type RankKind,
} from '../../api'
import { pickSymbol } from '../bus'
import { chgClass, fmtChg, fmtEok, fmtPrice } from '../format'
import { IntradayChart } from '../IntradayChart'
import { MiniCandles } from '../MiniCandles'

const REFRESH_MS = 60_000
const NEWS_N = 8
const RANK_N = 5

const RANKS: { key: RankKind; label: string }[] = [
  { key: 'gainers', label: '상승률' },
  { key: 'losers', label: '하락률' },
  { key: 'amount', label: '거래대금' },
  { key: 'volume', label: '거래량' },
  { key: 'marcap', label: '시가총액' },
]

function fmtQuote(price: number | null, unit: string): string {
  if (price == null) return '-'
  const digits = Math.abs(price) < 10000 ? 2 : 0
  return `${price.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })}${unit ? ` ${unit}` : ''}`
}

function fmtClock(d: Date | null): string {
  return d ? d.toLocaleTimeString('ko-KR', { hour12: false }) : '-'
}

/** 순위 기준이 된 값을 종목명 아래에 같이 보여준다 — 무슨 기준으로 1위인지 보이게 */
function rankMetric(kind: RankKind, it: RankItem): string {
  const where = `${it.market} · ${it.code}`
  if (kind === 'amount') return `${where} · 거래대금 ${fmtEok(it.amount)}`
  if (kind === 'volume') return `${where} · 거래량 ${Math.round(it.volume ?? 0).toLocaleString()}`
  if (kind === 'marcap') return `${where} · 시총 ${fmtEok(it.marcap)}`
  return where
}

function fmtFlow(v: number | null): string {
  if (v == null) return '-'
  return `${v > 0 ? '+' : ''}${Math.round(v).toLocaleString()}`
}

/** 코스피·코스닥 보드 — 지수 + 장중 흐름 + 투자자별 순매수 */
function BoardCard({ b }: { b: IndexBoard }) {
  const cls = chgClass(b.chg)
  return (
    <div className="board">
      <div className="hd">
        <b>{b.name}</b>
        <span className="live">● 장중</span>
      </div>
      <div className={`px ${cls}`}>{b.price == null ? '-' : b.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
      <div className={`dl ${cls}`}>
        {b.diff != null && `${b.diff > 0 ? '▲' : '▼'} ${Math.abs(b.diff).toFixed(2)}`} ({fmtChg(b.chg)})
      </div>
      <IntradayChart points={b.intraday} prevClose={b.prev_close} height={88} />
      {b.flow ? (
        <div className="flow">
          {(
            [
              ['외국인', b.flow.foreign],
              ['개인', b.flow.personal],
              ['기관', b.flow.institution],
            ] as const
          ).map(([label, v]) => (
            <div key={label}>
              <span className="k">{label}</span>
              <span className={`v ${chgClass(v)}`}>{fmtFlow(v)}</span>
            </div>
          ))}
        </div>
      ) : (
        <p className="hint">투자자별 순매수를 불러오지 못했습니다.</p>
      )}
      {b.flow && <p className="unit-note">순매수 단위 {b.flow.unit} · {b.flow.date}</p>}
    </div>
  )
}

function RankCard() {
  const [kind, setKind] = useState<RankKind>('gainers')
  const [items, setItems] = useState<RankItem[]>([])
  const [date, setDate] = useState('')
  const [msg, setMsg] = useState('')
  const req = useRef(0)

  useEffect(() => {
    const id = ++req.current
    setMsg('불러오는 중…')
    fetchRanking(kind, RANK_N)
      .then((r) => {
        if (id !== req.current) return
        setItems(r.items)
        setDate(r.date)
        setMsg('')
      })
      .catch((e: unknown) => {
        if (id === req.current) setMsg(e instanceof Error ? e.message : '순위 조회 실패')
      })
  }, [kind])

  return (
    <section className="card">
      <div className="hd">
        주식 순위
        <span className="sub">{date ? `${date} 종가 기준` : ''}</span>
      </div>
      <div className="bd">
        <div className="chips">
          {RANKS.map((r) => (
            <button key={r.key} className={`chip ${kind === r.key ? 'on' : ''}`} onClick={() => setKind(r.key)}>
              {r.label}
            </button>
          ))}
        </div>
        {msg && <p className="hint">{msg}</p>}
      </div>
      <div className="bd flush">
        {items.map((it, i) => (
          <div key={it.code} className="rankrow" onClick={() => pickSymbol({ code: it.code, name: it.name, market: it.market })}>
            <span className="no">{i + 1}</span>
            <span className="nm">
              {it.name}
              <small>{rankMetric(kind, it)}</small>
            </span>
            <span className="right">
              <b className="num">{fmtPrice(it.close)}</b>
              <span className={`pill ${chgClass(it.chg)}`}>{fmtChg(it.chg)}</span>
            </span>
          </div>
        ))}
      </div>
    </section>
  )
}

export function HomePanel() {
  const [boards, setBoards] = useState<IndexBoard[]>([])
  const [groups, setGroups] = useState<MarketGroup[]>([])
  const [news, setNews] = useState<NewsItem[]>([])
  const [updated, setUpdated] = useState<Date | null>(null)
  const [msg, setMsg] = useState('')
  const [loading, setLoading] = useState(false)
  const req = useRef(0)
  const alive = useRef(true)

  const load = useCallback(async () => {
    const id = ++req.current
    setLoading(true)
    // 하나가 실패해도 나머지는 그린다 — 조용히 빈 화면이 되는 걸 막는다
    const [b, g, n] = await Promise.allSettled([fetchIndexBoards(), fetchMarket(), fetchNews(undefined, NEWS_N)])
    if (id !== req.current || !alive.current) return
    if (b.status === 'fulfilled') setBoards(b.value)
    if (g.status === 'fulfilled') setGroups(g.value)
    if (n.status === 'fulfilled') setNews(n.value.slice(0, NEWS_N))
    const failed = [
      b.status === 'rejected' && '지수 보드',
      g.status === 'rejected' && '시장 지표',
      n.status === 'rejected' && '뉴스',
    ].filter(Boolean)
    setMsg(failed.length ? `${failed.join(' · ')} 조회 실패 — API 서버가 떠 있는지 확인하세요.` : '')
    setUpdated(new Date())
    setLoading(false)
  }, [])

  useEffect(() => {
    alive.current = true
    void load()
    const t = window.setInterval(() => void load(), REFRESH_MS)
    return () => {
      alive.current = false
      window.clearInterval(t)
    }
  }, [load])

  return (
    <div className="panel-col">
      <div className="toolbar">
        <span className="panel-title" style={{ margin: 0 }}>
          시장 개요
        </span>
        <span className="badge" style={{ marginLeft: 'auto' }}>
          {loading ? '갱신 중…' : `갱신 ${fmtClock(updated)}`}
        </span>
        <button className="ghost icon" onClick={() => void load()} title="지금 새로고침">
          ⟳
        </button>
      </div>

      <div className="panel-body">
        {msg && <p className="hint warn">{msg}</p>}

        <div className="boards">
          {boards.length === 0 && !msg ? (
            <p className="hint">지수를 불러오는 중…</p>
          ) : (
            boards.map((b) => <BoardCard key={b.key} b={b} />)
          )}
        </div>

        <RankCard />

        {groups.map((g) => (
          <section className="card" key={g.group}>
            <div className="hd">
              {g.group}
              <span className="sub">{g.items.length}개 지표</span>
            </div>
            <div className="bd">
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 10 }}>
                {g.items.map((it) => (
                  <div key={it.key} className="metric">
                    <div className="k" title={it.name}>
                      {it.name}
                    </div>
                    <div className="num v">{fmtQuote(it.price, it.unit)}</div>
                    <div className="r">
                      <span className={`num ${chgClass(it.chg)}`}>{fmtChg(it.chg)}</span>
                      <MiniCandles data={it.candles} width={78} height={28} />
                    </div>
                    <div className="asof">{it.asof ?? '-'}</div>
                  </div>
                ))}
              </div>
            </div>
          </section>
        ))}

        <section className="card">
          <div className="hd">
            증시 뉴스
            <span className="sub">한국경제 · 연합뉴스</span>
          </div>
          <div className="bd flush">
            {news.length === 0 ? (
              <p className="hint" style={{ padding: '0 16px' }}>
                표시할 뉴스가 없습니다.
              </p>
            ) : (
              <table className="grid">
                <tbody>
                  {news.map((n, i) => (
                    <tr key={`${n.url}-${i}`}>
                      <td onClick={() => window.open(n.url, '_blank', 'noopener')} title={n.title}>
                        {n.title}
                      </td>
                      <td className="num flat" style={{ whiteSpace: 'nowrap' }}>
                        {n.source} · {n.datetime}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </section>
      </div>
    </div>
  )
}
