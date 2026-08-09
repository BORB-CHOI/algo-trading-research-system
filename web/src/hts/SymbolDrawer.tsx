import { useEffect, useRef, useState } from 'react'
import { fetchNews, fetchQuotes, type NewsItem, type Quote } from '../api'
import { notifyWatchlistChanged, type SymbolPick } from './bus'
import { chgClass, fmtChg, fmtEok, fmtPrice } from './format'
import { MiniCandles } from './MiniCandles'

// 우측에서 밀려나오는 종목 요약. PC 는 폭이 남으니 차트를 가리지 않고 옆에 붙인다.
// "차트 크게" 를 누르면 차트 패널로 넘긴다 — 상세는 차트가, 요약은 여기가 맡는다.

const WL_KEY = 'hts-watchlist'
import { writeBoth } from './store'
const NEWS_N = 4

type Props = {
  sym: SymbolPick | null
  onClose: () => void
  onOpenChart: () => void
}

function addToWatchlist(sym: SymbolPick): string {
  let wl: Record<string, SymbolPick[]> = {}
  try {
    wl = JSON.parse(localStorage.getItem(WL_KEY) ?? '{}') as Record<string, SymbolPick[]>
  } catch {
    wl = {}
  }
  const group = Object.keys(wl)[0] ?? '관심종목1'
  const items = wl[group] ?? []
  if (items.some((i) => i.code === sym.code)) return `이미 [${group}] 에 있습니다.`
  writeBoth(WL_KEY, { ...wl, [group]: [...items, sym] })
  notifyWatchlistChanged()
  return `[${group}] 에 담았습니다.`
}

export function SymbolDrawer({ sym, onClose, onOpenChart }: Props) {
  const [shown, setShown] = useState(false)
  const [q, setQ] = useState<Quote | null>(null)
  const [news, setNews] = useState<NewsItem[]>([])
  const [msg, setMsg] = useState('')
  const req = useRef(0)

  useEffect(() => {
    if (!sym) return
    setShown(false)
    const raf = requestAnimationFrame(() => setShown(true)) // 마운트 후 한 프레임 뒤에 트랜지션 시작
    return () => cancelAnimationFrame(raf)
  }, [sym?.code])

  useEffect(() => {
    if (!sym) return
    const id = ++req.current
    setMsg('')
    setQ(null)
    setNews([])
    const loadQuote = () =>
      void fetchQuotes([sym.code], true).then((r) => {
        if (id === req.current) setQ(r.quotes[0] ?? null)
      })
    loadQuote()
    const t = window.setInterval(loadQuote, 10_000) // 실시간 시세 폴링
    void fetchNews(sym.code, NEWS_N)
      .then((n) => {
        if (id === req.current) setNews(n)
      })
      .catch(() => {})
    return () => window.clearInterval(t)
  }, [sym?.code])

  useEffect(() => {
    function esc(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  }, [onClose])

  if (!sym) return null
  const cls = chgClass(q?.chg)
  const diff = q && q.chg != null ? (q.close * q.chg) / (100 + q.chg) : null

  return (
    <aside className={`drawer ${shown ? 'open' : ''}`}>
      <div className="hd">
        <div>
          <b>{sym.name}</b>
          <small>
            {sym.market || '—'} · {sym.code}
          </small>
        </div>
        <button className="ghost icon" onClick={onClose} title="닫기 (Esc)">
          ✕
        </button>
      </div>

      <div className="bd">
        <div className={`px ${cls}`}>{q ? fmtPrice(q.close) : '—'}</div>
        <div className={`dl ${cls}`}>
          {diff != null && `${diff > 0 ? '▲' : '▼'} ${fmtPrice(Math.abs(diff))}`} ({fmtChg(q?.chg)})
        </div>
        <div style={{ margin: '10px 0 4px' }}>
          <MiniCandles data={q?.candles} width={300} height={78} />
        </div>
        <p className="hint" style={{ textAlign: 'right', margin: 0 }}>
          최근 30 거래일
        </p>

        <dl className="facts">
          <div>
            <dt>거래량</dt>
            <dd>{q?.volume != null ? Math.round(q.volume).toLocaleString() : '-'}</dd>
          </div>
          <div>
            <dt>거래대금</dt>
            <dd>{q ? fmtEok(q.amount) : '-'}</dd>
          </div>
          <div>
            <dt>시가총액</dt>
            <dd>{q ? fmtEok(q.marcap) : '-'}</dd>
          </div>
        </dl>

        <p className="panel-title" style={{ margin: '14px 0 4px' }}>
          종목 뉴스
        </p>
        {news.length === 0 ? (
          <p className="hint">표시할 뉴스가 없습니다.</p>
        ) : (
          news.map((n, i) => (
            <button key={`${n.url}-${i}`} className="newsrow" onClick={() => window.open(n.url, '_blank', 'noopener')}>
              <span className="t">{n.title}</span>
              <span className="s">
                {n.source} · {n.datetime}
              </span>
            </button>
          ))
        )}
        {msg && <p className="hint">{msg}</p>}
      </div>

      <div className="actionbar">
        <button onClick={() => setMsg(addToWatchlist(sym))}>관심종목</button>
        <button className="cta" onClick={onOpenChart}>
          차트 크게 보기
        </button>
      </div>
    </aside>
  )
}
