import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchQuotes, type Quote } from '../../api'
import { notifyWatchlistChanged, onWatchlistChanged, pickSymbol, type SymbolPick } from '../bus'
import { chgClass, fmtChg, fmtEok, fmtPrice } from '../format'

// 관심그룹 선택 + 그룹별 종목 시세(/api/quotes). 저장은 localStorage(형식 고정 — 조건검색이 같은 걸 쓴다).
type Watchlist = Record<string, SymbolPick[]>

const WL_KEY = 'hts-watchlist'
const MAX_HITS = 8
const DEFAULT_GROUP = '관심종목1'

function loadWl(): Watchlist {
  try {
    return JSON.parse(localStorage.getItem(WL_KEY) ?? '{}') as Watchlist
  } catch {
    return {}
  }
}

export function WatchlistPanel() {
  const [wl, setWl] = useState<Watchlist>(loadWl)
  const [group, setGroup] = useState(() => Object.keys(loadWl())[0] ?? DEFAULT_GROUP)
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<SymbolPick[]>([])
  const [quotes, setQuotes] = useState<Map<string, Quote>>(new Map())
  const [quoteDate, setQuoteDate] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [newName, setNewName] = useState('')
  const [renaming, setRenaming] = useState(false)
  const [renameTo, setRenameTo] = useState('')
  const [confirmDel, setConfirmDel] = useState(false)
  const [error, setError] = useState('')
  const quoteReq = useRef(0)

  const items = wl[group] ?? []
  const groupNames = Object.keys(wl)
  if (!groupNames.includes(group)) groupNames.unshift(group)

  const refreshQuotes = useCallback(async (codes: string[]) => {
    const req = ++quoteReq.current
    try {
      const { date, quotes } = await fetchQuotes(codes)
      if (req !== quoteReq.current) return
      setQuoteDate(date)
      setQuotes(new Map(quotes.map((it) => [it.code, it])))
      setError('')
    } catch {
      if (req === quoteReq.current) setError('시세 조회 실패 — ⟳ 로 재시도하세요.')
    }
  }, [])

  useEffect(() => {
    void refreshQuotes(items.map((i) => i.code))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [group, wl])

  useEffect(() => onWatchlistChanged(() => setWl(loadWl())), [])

  function save(next: Watchlist) {
    setWl(next)
    localStorage.setItem(WL_KEY, JSON.stringify(next))
    notifyWatchlistChanged()
  }

  function selectGroup(name: string) {
    setGroup(name)
    setConfirmDel(false)
    setRenaming(false)
  }

  function resetEdits() {
    setAdding(false)
    setNewName('')
    setRenaming(false)
    setRenameTo('')
    setConfirmDel(false)
  }

  function addGroup() {
    const name = newName.trim()
    if (!name) return
    if (!wl[name]) save({ ...wl, [name]: [] })
    selectGroup(name)
    setAdding(false)
    setNewName('')
  }

  // 이름 변경 — 키 순서를 그대로 유지한 채 키만 바꾼다.
  function renameGroup() {
    const name = renameTo.trim()
    if (!name || name === group) return resetEdits()
    if (wl[name]) {
      setError('같은 이름의 그룹이 이미 있습니다.')
      return
    }
    const next: Watchlist = {}
    for (const [k, v] of Object.entries(wl)) next[k === group ? name : k] = v
    if (!(name in next)) next[name] = items
    save(next)
    selectGroup(name)
    setRenameTo('')
  }

  function deleteGroup() {
    const next = { ...wl }
    delete next[group]
    save(next)
    selectGroup(Object.keys(next)[0] ?? DEFAULT_GROUP)
  }

  async function search() {
    try {
      const res = await fetch(`/api/symbols?q=${encodeURIComponent(q)}`)
      if (!res.ok) throw new Error(`검색 실패 (${res.status})`)
      const { symbols } = (await res.json()) as { symbols: { ticker: string; name: string; market: string }[] }
      setHits(symbols.map((s) => ({ code: s.ticker, name: s.name, market: s.market })))
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : '종목 검색 실패')
    }
  }

  return (
    <div className="panel-col">
      <div className="toolbar">
        <span className="panel-title" style={{ margin: 0 }}>
          관심그룹 선택
        </span>
        <span className="badge">{groupNames.length}개 그룹</span>
        <button style={{ marginLeft: 'auto' }} onClick={() => void refreshQuotes(items.map((i) => i.code))} title="시세 갱신">
          ⟳
        </button>
      </div>
      <div className="panel-body">
        <div
          style={{
            maxHeight: 190,
            overflowY: 'auto',
            border: '1px solid var(--hts-border)',
            borderRadius: 4,
            marginBottom: 6,
          }}
        >
          <table className="grid">
            <tbody>
              {groupNames.map((g) => {
                const n = (wl[g] ?? []).length
                const on = g === group
                return (
                  <tr key={g} className={on ? 'selected' : undefined}>
                    <td
                      onClick={() => selectGroup(g)}
                      style={{
                        cursor: 'pointer',
                        color: n > 0 ? 'var(--hts-accent)' : 'var(--hts-text-2)',
                        fontWeight: on ? 700 : 400,
                      }}
                    >
                      {on ? '▶ ' : ''}
                      {g}
                      <span className="flat" style={{ marginLeft: 4, fontWeight: 400 }}>
                        ({n})
                      </span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

        {adding ? (
          <div className="form-row">
            <input
              autoFocus
              placeholder="새 그룹명"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => (e.key === 'Enter' ? addGroup() : e.key === 'Escape' ? resetEdits() : undefined)}
            />
            <button onClick={addGroup}>추가</button>
            <button onClick={resetEdits}>취소</button>
          </div>
        ) : renaming ? (
          <div className="form-row">
            <input
              autoFocus
              placeholder={`[${group}] 새 이름`}
              value={renameTo}
              onChange={(e) => setRenameTo(e.target.value)}
              onKeyDown={(e) => (e.key === 'Enter' ? renameGroup() : e.key === 'Escape' ? resetEdits() : undefined)}
            />
            <button onClick={renameGroup}>변경</button>
            <button onClick={resetEdits}>취소</button>
          </div>
        ) : confirmDel ? (
          <div className="form-row">
            <span className="hint" style={{ flex: 1 }}>
              [{group}] 그룹({items.length}종목)을 삭제할까요?
            </span>
            <button onClick={deleteGroup}>정말 삭제</button>
            <button onClick={resetEdits}>취소</button>
          </div>
        ) : (
          <div className="form-row">
            <button onClick={() => setAdding(true)} title="새 그룹 만들기">
              +그룹
            </button>
            <button
              onClick={() => {
                setRenameTo(group)
                setRenaming(true)
              }}
              title="현재 그룹 이름 변경"
            >
              이름변경
            </button>
            <button onClick={() => setConfirmDel(true)} title="현재 그룹 삭제">
              그룹삭제
            </button>
          </div>
        )}

        <div className="form-row">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="종목 검색" onKeyDown={(e) => e.key === 'Enter' && search()} />
          <button onClick={search}>검색</button>
        </div>
        {error && <p className="hint">{error}</p>}
        {hits.length > 0 && (
          <table className="grid">
            <tbody>
              {hits.slice(0, MAX_HITS).map((h) => (
                <tr key={h.code}>
                  <td onClick={() => pickSymbol(h)} style={{ cursor: 'pointer' }}>
                    {h.name}
                  </td>
                  <td className="flat">{h.code}</td>
                  <td className="num">
                    <button onClick={() => save({ ...wl, [group]: [...items.filter((i) => i.code !== h.code), h] })}>
                      +담기
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {hits.length > MAX_HITS && (
          <p className="hint">
            검색 결과 {hits.length}건 중 {MAX_HITS}건만 표시합니다.
          </p>
        )}

        <p className="hint">
          [{group}] {items.length}종목{quoteDate ? ` · ${quoteDate} 기준` : ''}
        </p>
        {items.length === 0 ? (
          <p className="hint">그룹이 비어 있습니다. 위에서 종목을 검색해 담아 보세요.</p>
        ) : (
          <table className="grid">
            <thead>
              <tr>
                <th>종목명</th>
                <th className="num">현재가</th>
                <th className="num">등락률</th>
                <th className="num">거래대금</th>
                <th className="num" />
              </tr>
            </thead>
            <tbody>
              {items.map((it) => {
                const qt = quotes.get(it.code)
                return (
                  <tr key={it.code}>
                    <td onClick={() => pickSymbol(it)} style={{ cursor: 'pointer' }}>
                      {it.name}
                    </td>
                    <td className={`num ${chgClass(qt?.chg)}`}>{qt ? fmtPrice(qt.close) : '-'}</td>
                    <td className={`num ${chgClass(qt?.chg)}`}>{fmtChg(qt?.chg)}</td>
                    <td className="num">{qt ? fmtEok(qt.amount) : '-'}</td>
                    <td className="num">
                      <button
                        title="삭제"
                        className="row-del"
                        onClick={() => save({ ...wl, [group]: items.filter((i) => i.code !== it.code) })}
                      >
                        ×
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
