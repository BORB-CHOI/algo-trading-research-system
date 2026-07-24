import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchQuotes, type Quote } from '../../api'
import { pickSymbol, type SymbolPick } from '../bus'
import { chgClass, fmtChg, fmtEok, fmtPrice } from '../format'

// 관심종목(테마) 패널 — 그룹별 종목 리스트 + 시세(/api/quotes). 저장은 localStorage.
type Watchlist = Record<string, SymbolPick[]>

const WL_KEY = 'hts-watchlist'
const MAX_HITS = 8 // 검색 히트 표시 상한

function loadWl(): Watchlist {
  try {
    return JSON.parse(localStorage.getItem(WL_KEY) ?? '{}') as Watchlist
  } catch {
    return {}
  }
}

export function WatchlistPanel() {
  const [wl, setWl] = useState<Watchlist>(loadWl)
  const [group, setGroup] = useState(() => Object.keys(loadWl())[0] ?? '관심종목1')
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<SymbolPick[]>([])
  const [quotes, setQuotes] = useState<Map<string, Quote>>(new Map())
  const [quoteDate, setQuoteDate] = useState<string | null>(null)
  // 새 그룹 인라인 입력 토글 (prompt 창 대신)
  const [adding, setAdding] = useState(false)
  const [newName, setNewName] = useState('')
  // 그룹 삭제 2단계 확인 (confirm 창 대신: 삭제 → 정말 삭제/취소)
  const [confirmDel, setConfirmDel] = useState(false)
  const [error, setError] = useState('')
  // 그룹 빠른 전환 시 늦게 도착한 이전 응답이 최신 시세를 덮지 않게 요청 순번으로 가드
  const quoteReq = useRef(0)

  const items = wl[group] ?? []
  // 아직 저장 전인 기본 그룹도 select 에 보이게 한다.
  const groupOptions = Object.keys(wl)
  if (!groupOptions.includes(group)) groupOptions.unshift(group)

  const refreshQuotes = useCallback(async (codes: string[]) => {
    const req = ++quoteReq.current
    try {
      const { date, quotes } = await fetchQuotes(codes)
      if (req !== quoteReq.current) return // 더 새 요청이 이미 나갔다
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

  function save(next: Watchlist) {
    setWl(next)
    localStorage.setItem(WL_KEY, JSON.stringify(next))
  }

  function selectGroup(name: string) {
    setGroup(name)
    setConfirmDel(false)
  }

  function addGroup() {
    const name = newName.trim()
    if (!name) return
    if (!wl[name]) save({ ...wl, [name]: [] })
    selectGroup(name)
    setNewName('')
    setAdding(false)
  }

  // 그룹 삭제 — 그룹을 비우고 키 자체를 제거한다.
  function deleteGroup() {
    const next = { ...wl }
    delete next[group]
    save(next)
    selectGroup(Object.keys(next)[0] ?? '관심종목1')
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
        <select value={group} onChange={(e) => selectGroup(e.target.value)} title="관심 그룹">
          {groupOptions.map((g) => (
            <option key={g} value={g}>
              {g}
            </option>
          ))}
        </select>
        {adding ? (
          <>
            <input
              size={8}
              autoFocus
              placeholder="새 그룹명"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && addGroup()}
            />
            <button onClick={addGroup}>추가</button>
            <button
              onClick={() => {
                setAdding(false)
                setNewName('')
              }}
            >
              취소
            </button>
          </>
        ) : confirmDel ? (
          <>
            <button onClick={deleteGroup} title={`[${group}] 그룹을 비우고 제거`}>
              정말 삭제
            </button>
            <button onClick={() => setConfirmDel(false)}>취소</button>
          </>
        ) : (
          <>
            <button onClick={() => setAdding(true)} title="새 그룹 만들기">
              +그룹
            </button>
            <button onClick={() => setConfirmDel(true)} title="현재 그룹 삭제">
              그룹삭제
            </button>
            <button onClick={() => void refreshQuotes(items.map((i) => i.code))} title="시세 갱신">
              ⟳
            </button>
          </>
        )}
      </div>
      <div className="panel-body">
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
                <td onClick={() => pickSymbol(h)}>{h.name}</td>
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
                  <td onClick={() => pickSymbol(it)}>{it.name}</td>
                  <td className={`num ${chgClass(qt?.chg)}`}>{qt ? fmtPrice(qt.close) : '-'}</td>
                  <td className={`num ${chgClass(qt?.chg)}`}>{fmtChg(qt?.chg)}</td>
                  <td className="num">{qt ? fmtEok(qt.amount) : '-'}</td>
                  <td className="num">
                    {/* hover 시에만 보이는 삭제 버튼 — 표시는 CSS(.row-del)가, 클릭 가능 여부도 함께 제어 */}
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
