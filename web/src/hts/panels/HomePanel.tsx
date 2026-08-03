import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchMarket, fetchNews, type MarketGroup, type NewsItem } from '../../api'
import { chgClass, fmtChg } from '../format'

const REFRESH_MS = 60_000
const NEWS_N = 8

function fmtQuote(price: number | null, unit: string): string {
  if (price == null) return '-'
  const digits = Math.abs(price) < 100 ? 2 : Math.abs(price) < 10000 ? 2 : 0
  return `${price.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })}${unit ? ` ${unit}` : ''}`
}

function fmtClock(d: Date | null): string {
  return d ? d.toLocaleTimeString('ko-KR', { hour12: false }) : '-'
}

export function HomePanel() {
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
    try {
      const [g, n] = await Promise.all([fetchMarket(), fetchNews(undefined, NEWS_N)])
      if (id !== req.current || !alive.current) return
      setGroups(g)
      setNews(n.slice(0, NEWS_N))
      setUpdated(new Date())
      setMsg('')
    } catch (e) {
      if (id === req.current && alive.current) setMsg(e instanceof Error ? e.message : '시장 조회 실패')
    } finally {
      if (id === req.current && alive.current) setLoading(false)
    }
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
        <button onClick={() => void load()} title="지금 새로고침">
          ⟳
        </button>
      </div>
      <div className="panel-body">
        {msg && <p className="hint">{msg}</p>}
        {groups.length === 0 && !msg && <p className="hint">시장 지표를 불러오는 중…</p>}

        {groups.map((g) => (
          <section key={g.group} style={{ marginBottom: 14 }}>
            <h4 style={{ margin: '0 0 6px' }}>{g.group}</h4>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))',
                gap: 6,
              }}
            >
              {g.items.map((it) => (
                <div
                  key={it.key}
                  style={{
                    background: 'var(--hts-elev)',
                    border: '1px solid var(--hts-border)',
                    borderRadius: 4,
                    padding: '7px 9px',
                    minWidth: 0,
                  }}
                >
                  <div
                    style={{
                      fontSize: 11,
                      color: 'var(--hts-text-2)',
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                    }}
                    title={it.name}
                  >
                    {it.name}
                  </div>
                  <div
                    className="num"
                    style={{ fontSize: 15, fontWeight: 700, fontVariantNumeric: 'tabular-nums', marginTop: 2 }}
                  >
                    {fmtQuote(it.price, it.unit)}
                  </div>
                  <div
                    className={chgClass(it.chg)}
                    style={{ fontSize: 12, fontVariantNumeric: 'tabular-nums' }}
                  >
                    {fmtChg(it.chg)}
                  </div>
                  <div style={{ fontSize: 10, color: 'var(--hts-text-3)' }}>{it.asof ?? '-'}</div>
                </div>
              ))}
            </div>
          </section>
        ))}

        <h4 style={{ margin: '0 0 4px' }}>증시 뉴스</h4>
        {news.length === 0 ? (
          <p className="hint">표시할 뉴스가 없습니다.</p>
        ) : (
          <table className="grid">
            <tbody>
              {news.map((n, i) => (
                <tr key={`${n.url}-${i}`}>
                  <td
                    onClick={() => window.open(n.url, '_blank', 'noopener')}
                    title={n.title}
                    style={{ cursor: 'pointer' }}
                  >
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
    </div>
  )
}
