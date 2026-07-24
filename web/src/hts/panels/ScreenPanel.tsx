import { useMemo, useState } from 'react'
import { fetchScreen, type ScreenResponse } from '../../api'
import { pickSymbol } from '../bus'
import { EOK, chgClass, fmtChg, fmtEok, fmtPrice } from '../format'

// 조건검색 패널 — 임계값은 사용자가 매번 입력한다(서버 하드코딩 없음, CLAUDE.md).

type SortKey = 'chg' | 'amount' | 'marcap'

export function ScreenPanel() {
  const [date, setDate] = useState('')
  const [minAmount, setMinAmount] = useState('')
  const [minMarcap, setMinMarcap] = useState('')
  const [maxMarcap, setMaxMarcap] = useState('')
  const [result, setResult] = useState<ScreenResponse | null>(null)
  const [msg, setMsg] = useState('')
  // 헤더 클릭 정렬 상태 — 같은 키 재클릭 시 방향 토글, 새 키는 내림차순부터.
  const [sortKey, setSortKey] = useState<SortKey | null>(null)
  const [sortDesc, setSortDesc] = useState(true)

  async function run() {
    setMsg('조회 중…')
    try {
      const num = (s: string) => (s.trim() ? Number(s) * EOK : undefined)
      const r = await fetchScreen({
        date: date || undefined,
        minAmount: num(minAmount),
        minMarcap: num(minMarcap),
        maxMarcap: num(maxMarcap),
      })
      setResult(r)
      setMsg(
        r.items.length === 0
          ? `${r.date} 기준 조건에 맞는 종목이 없습니다.`
          : `${r.date} 기준 ${r.total}종목 (거래대금순 상위 ${r.items.length})`,
      )
    } catch (e) {
      setMsg(e instanceof Error ? e.message : '조회 실패')
    }
  }

  function toggleSort(k: SortKey) {
    if (sortKey === k) {
      setSortDesc((d) => !d)
    } else {
      setSortKey(k)
      setSortDesc(true)
    }
  }

  // 클라이언트 정렬 — chg 는 null 가능(연초 첫 거래일 등)이라 null 은 항상 뒤로.
  const rows = useMemo(() => {
    const items = result?.items ?? []
    if (!sortKey) return items
    return [...items].sort((a, b) => {
      const av = a[sortKey]
      const bv = b[sortKey]
      if (av == null && bv == null) return 0
      if (av == null) return 1
      if (bv == null) return -1
      return sortDesc ? bv - av : av - bv
    })
  }, [result, sortKey, sortDesc])

  const arrow = (k: SortKey) => (sortKey === k ? (sortDesc ? ' ▼' : ' ▲') : '')

  return (
    <div className="panel-col">
      <div className="toolbar">
        <input type="date" value={date} onChange={(e) => setDate(e.target.value)} title="기준일 (빈칸 = 최신 거래일)" />
        <input size={9} placeholder="거래대금≥(억)" value={minAmount} onChange={(e) => setMinAmount(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && run()} />
        <input size={7} placeholder="시총≥(억)" value={minMarcap} onChange={(e) => setMinMarcap(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && run()} />
        <input size={7} placeholder="시총≤(억)" value={maxMarcap} onChange={(e) => setMaxMarcap(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && run()} />
        <button onClick={run}>검색</button>
      </div>
      <div className="panel-body">
      <p className="hint">{msg || '조건을 입력하고 검색을 누르면 결과가 표시됩니다.'}</p>
      {rows.length > 0 && (
        <table className="grid">
          <thead>
            <tr>
              <th>종목명</th>
              <th className="num">현재가</th>
              <th className="num" style={{ cursor: 'pointer' }} title="클릭 정렬" onClick={() => toggleSort('chg')}>
                등락률{arrow('chg')}
              </th>
              <th className="num" style={{ cursor: 'pointer' }} title="클릭 정렬" onClick={() => toggleSort('amount')}>
                거래대금{arrow('amount')}
              </th>
              <th className="num" style={{ cursor: 'pointer' }} title="클릭 정렬" onClick={() => toggleSort('marcap')}>
                시총{arrow('marcap')}
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((it) => (
              <tr key={it.code} onClick={() => pickSymbol({ code: it.code, name: it.name, market: it.market })}>
                <td>{it.name}</td>
                <td className={`num ${chgClass(it.chg)}`}>{fmtPrice(it.close)}</td>
                <td className={`num ${chgClass(it.chg)}`}>{fmtChg(it.chg)}</td>
                <td className="num">{fmtEok(it.amount)}</td>
                <td className="num">{fmtEok(it.marcap)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      </div>
    </div>
  )
}
