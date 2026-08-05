import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import useEmblaCarousel from 'embla-carousel-react'
import { fetchQuotes, type Quote, type Symbol } from '../../api'
import { notifyWatchlistChanged, onSymbolPick, onWatchlistChanged, pickSymbol, type SymbolPick } from '../bus'
import { chgClass, fmtChg, fmtEok, fmtPrice } from '../format'
import { MiniCandles } from '../MiniCandles'
import { SearchModal } from '../components/SearchModal'

// 관심종목 — 그룹을 하나씩 갈아 끼우는 대신 [전체] 에서 모든 그룹을 접었다 폈다 하며 본다.
// 상단 탭은 좌우로 슬라이드해 넘긴다(embla). 저장 형식은 고정 — 다른 패널이 같은 걸 읽는다.

type Watchlist = Record<string, SymbolPick[]>

const WL_KEY = 'hts-watchlist'
const COLLAPSE_KEY = 'hts-watchlist-collapsed'
const ALL = '__all__'
const RECENT = '__recent__'
const BEST = '__best__'
const RECENT_KEY = 'hts-recent-symbols'
const RECENT_MAX = 20

function loadWl(): Watchlist {
  try {
    return JSON.parse(localStorage.getItem(WL_KEY) ?? '{}') as Watchlist
  } catch {
    return {}
  }
}

function loadRecent(): SymbolPick[] {
  try {
    return JSON.parse(localStorage.getItem(RECENT_KEY) ?? '[]') as SymbolPick[]
  } catch {
    return []
  }
}

function loadCollapsed(): string[] {
  try {
    return JSON.parse(localStorage.getItem(COLLAPSE_KEY) ?? '[]') as string[]
  } catch {
    return []
  }
}

/** 종목 행 — 이미지처럼 [종목명 / 시장·코드] + [현재가] + [등락·등락률] + 미니차트 */
function Row(props: { it: SymbolPick; q: Quote | undefined; onDelete?: () => void }) {
  const { it, q } = props
  const diff = q && q.chg != null ? (q.close * q.chg) / (100 + q.chg) : null
  const cls = chgClass(q?.chg)
  return (
    <motion.div
      className="wl-row"
      layout
      initial={{ opacity: 0, y: 3 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, height: 0 }}
      transition={{ duration: 0.16 }}
      onClick={() => pickSymbol(it)}
    >
      <span className="nm">
        {it.name}
        <small>
          <em className={it.market === 'KOSPI' ? 'kospi' : 'kosdaq'}>{it.market || '—'}</em>
          {it.code}
        </small>
      </span>
      <span className="spark">
        <MiniCandles data={q?.candles} width={54} height={20} />
      </span>
      <span className={`px num ${cls}`}>{q ? fmtPrice(q.close) : '-'}</span>
      <span className={`chg num ${cls}`}>
        <b>{fmtChg(q?.chg)}</b>
        <small>{diff != null ? `${diff > 0 ? '▲' : '▼'} ${fmtPrice(Math.abs(diff))}` : '-'}</small>
      </span>
      <span className="amt num">{q ? fmtEok(q.amount) : '-'}</span>
      {props.onDelete && (
        <button
          className="ghost del"
          title="빼기"
          onClick={(e) => {
            e.stopPropagation()
            props.onDelete?.()
          }}
        >
          ✕
        </button>
      )}
    </motion.div>
  )
}

export function WatchlistPanel() {
  const [wl, setWl] = useState<Watchlist>(loadWl)
  const [recent, setRecent] = useState<SymbolPick[]>(loadRecent)
  const [best, setBest] = useState<SymbolPick[]>([])
  const [tab, setTab] = useState<string>(ALL)
  const [collapsed, setCollapsed] = useState<string[]>(loadCollapsed)
  const [quotes, setQuotes] = useState<Map<string, Quote>>(new Map())
  const [quoteDate, setQuoteDate] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  const [addTo, setAddTo] = useState<string>('')
  const [editing, setEditing] = useState(false)
  const [newName, setNewName] = useState('')
  const quoteReq = useRef(0)

  const groups = Object.keys(wl)
  const [emblaRef] = useEmblaCarousel({ dragFree: true, containScroll: 'trimSnaps' })

  // 최근조회 — 어디서 종목을 고르든 쌓인다
  useEffect(
    () =>
      onSymbolPick((s) => {
        setRecent((prev) => {
          const next = [s, ...prev.filter((p) => p.code !== s.code)].slice(0, RECENT_MAX)
          localStorage.setItem(RECENT_KEY, JSON.stringify(next))
          return next
        })
      }),
    [],
  )

  useEffect(() => onWatchlistChanged(() => setWl(loadWl())), [])

  // 실시간 Best — 거래대금 상위 (marcap 최신 거래일 기준)
  useEffect(() => {
    let alive = true
    fetch('/api/ranking?kind=amount&limit=15')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d: { items: { code: string; name: string; market: string }[] }) => {
        if (alive) setBest(d.items.map((i) => ({ code: i.code, name: i.name, market: i.market })))
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [])

  // 화면에 보이는 모든 종목의 시세를 한 번에 받는다 (그룹별로 나눠 부르지 않는다)
  const visible = useMemo(() => {
    const seen = new Map<string, SymbolPick>()
    const push = (list: SymbolPick[]) => list.forEach((i) => seen.set(i.code, i))
    if (tab === ALL) groups.forEach((g) => push(wl[g] ?? []))
    else if (tab === RECENT) push(recent)
    else if (tab === BEST) push(best)
    else push(wl[tab] ?? [])
    return [...seen.values()]
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wl, tab, recent, best])

  const refreshQuotes = useCallback(async (codes: string[]) => {
    const req = ++quoteReq.current
    if (codes.length === 0) {
      setQuotes(new Map())
      return
    }
    try {
      const { date, quotes } = await fetchQuotes(codes, true)
      if (req !== quoteReq.current) return
      setQuoteDate(date)
      setQuotes(new Map(quotes.map((it) => [it.code, it])))
      setError('')
    } catch {
      if (req === quoteReq.current) setError('시세 조회 실패 — ⟳ 로 재시도하세요.')
    }
  }, [])

  useEffect(() => {
    void refreshQuotes(visible.map((i) => i.code))
    // 실시간 시세(네이버 폴링) — 서버 캐시 5초라 10초면 충분히 신선하다
    const t = window.setInterval(() => void refreshQuotes(visible.map((i) => i.code)), 10_000)
    return () => window.clearInterval(t)
  }, [visible, refreshQuotes])

  function save(next: Watchlist) {
    setWl(next)
    localStorage.setItem(WL_KEY, JSON.stringify(next))
    notifyWatchlistChanged()
  }

  function toggleGroup(g: string) {
    setCollapsed((prev) => {
      const next = prev.includes(g) ? prev.filter((x) => x !== g) : [...prev, g]
      localStorage.setItem(COLLAPSE_KEY, JSON.stringify(next))
      return next
    })
  }

  function addGroup() {
    const name = newName.trim()
    if (!name || wl[name]) return
    save({ ...wl, [name]: [] })
    setNewName('')
    setTab(name)
  }

  function addSymbol(group: string, s: Symbol) {
    const pick: SymbolPick = { code: s.code, name: s.name, market: s.market }
    const items = wl[group] ?? []
    if (items.some((i) => i.code === s.code)) return
    save({ ...wl, [group]: [...items, pick] })
  }

  const tabs = [
    { key: ALL, label: '전체', n: groups.reduce((a, g) => a + (wl[g]?.length ?? 0), 0) },
    { key: RECENT, label: '최근조회', n: recent.length },
    { key: BEST, label: '실시간Best', n: best.length },
    ...groups.map((g) => ({ key: g, label: g, n: (wl[g] ?? []).length })),
  ]

  function openAdd(group: string) {
    setAddTo(group)
    setSearchOpen(true)
  }

  return (
    <div className="panel-col">
      <div className="wl-tabs" ref={emblaRef}>
        <div className="track">
          {tabs.map((t) => (
            <button key={t.key} className={`tab ${tab === t.key ? 'on' : ''}`} onClick={() => setTab(t.key)}>
              {t.label}
              <span className="n">{t.n}</span>
            </button>
          ))}
          <button className="tab add" title="그룹 추가" onClick={() => setEditing((v) => !v)}>
            ＋
          </button>
        </div>
      </div>

      <div className="toolbar">
        <span className="badge on">{visible.length}종목</span>
        {quoteDate && <span className="badge">{quoteDate}</span>}
        <button style={{ marginLeft: 'auto' }} onClick={() => openAdd(tab === ALL ? (groups[0] ?? '') : tab)}>
          종목추가 ＋
        </button>
        <button className="ghost icon" onClick={() => void refreshQuotes(visible.map((i) => i.code))} title="시세 갱신">
          ⟳
        </button>
      </div>

      <AnimatePresence>
        {editing && (
          <motion.div
            className="form-row"
            style={{ padding: '8px 10px', margin: 0, overflow: 'hidden' }}
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18 }}
          >
            <input
              autoFocus
              placeholder="새 그룹명 (예: 로봇, AI, 반도체)"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && addGroup()}
            />
            <button className="cta" onClick={addGroup}>
              추가
            </button>
            <button className="ghost" onClick={() => setEditing(false)}>
              닫기
            </button>
          </motion.div>
        )}
      </AnimatePresence>

      <div className="panel-body plain">
        {error && <p className="hint warn">{error}</p>}

        {tab === ALL ? (
          groups.length === 0 ? (
            <p className="hint">그룹이 없습니다. 위 ＋ 로 그룹을 만들고 종목을 담아 보세요.</p>
          ) : (
            groups.map((g) => {
              const items = wl[g] ?? []
              const open = !collapsed.includes(g)
              return (
                <section className="wl-group" key={g}>
                  <header onClick={() => toggleGroup(g)}>
                    <motion.span className="caret" animate={{ rotate: open ? 90 : 0 }} transition={{ duration: 0.16 }}>
                      ▶
                    </motion.span>
                    <b>{g}</b>
                    <span className="n">{items.length}</span>
                    <span className="grow" />
                    <button
                      className="ghost"
                      onClick={(e) => {
                        e.stopPropagation()
                        openAdd(g)
                      }}
                    >
                      ＋
                    </button>
                    <button
                      className="ghost"
                      title="그룹 삭제"
                      onClick={(e) => {
                        e.stopPropagation()
                        const next = { ...wl }
                        delete next[g]
                        save(next)
                      }}
                    >
                      ✕
                    </button>
                  </header>
                  <AnimatePresence initial={false}>
                    {open && (
                      <motion.div
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: 'auto', opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        transition={{ duration: 0.18, ease: [0.22, 0.61, 0.36, 1] }}
                        style={{ overflow: 'hidden' }}
                      >
                        {items.length === 0 ? (
                          <p className="hint" style={{ padding: '6px 10px' }}>
                            비어 있습니다.
                          </p>
                        ) : (
                          items.map((it) => (
                            <Row
                              key={it.code}
                              it={it}
                              q={quotes.get(it.code)}
                              onDelete={() => save({ ...wl, [g]: items.filter((x) => x.code !== it.code) })}
                            />
                          ))
                        )}
                      </motion.div>
                    )}
                  </AnimatePresence>
                </section>
              )
            })
          )
        ) : (
          <AnimatePresence mode="wait">
            <motion.div
              key={tab}
              initial={{ opacity: 0, x: 12 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -12 }}
              transition={{ duration: 0.18 }}
            >
              {visible.length === 0 ? (
                <p className="hint">표시할 종목이 없습니다.</p>
              ) : (
                visible.map((it) => (
                  <Row
                    key={it.code}
                    it={it}
                    q={quotes.get(it.code)}
                    onDelete={
                      tab === RECENT || tab === BEST
                        ? undefined
                        : () => save({ ...wl, [tab]: (wl[tab] ?? []).filter((x) => x.code !== it.code) })
                    }
                  />
                ))
              )}
            </motion.div>
          </AnimatePresence>
        )}
      </div>

      <SearchModal
        open={searchOpen}
        onClose={() => setSearchOpen(false)}
        onPick={(s) => pickSymbol({ code: s.code, name: s.name, market: s.market })}
        trailing={(s) =>
          addTo ? (
            <button
              className="add"
              onClick={(e) => {
                e.stopPropagation()
                addSymbol(addTo, s)
              }}
            >
              담기
            </button>
          ) : null
        }
      />
    </div>
  )
}
