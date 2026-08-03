import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchFinancials, type FinancialRow } from '../../api'
import { currentSymbol, onSymbolPick, type SymbolPick } from '../bus'
import { fmtEok } from '../format'

const ACCOUNTS = ['매출액', '영업이익', '당기순이익', '자산총계', '부채총계', '자본총계'] as const

function growth(cur: number | null, prev: number | null): number | null {
  if (cur == null || prev == null || prev === 0) return null
  return (cur / Math.abs(prev) - 1) * 100
}

export function FinancialsPanel() {
  const [symbol, setSymbol] = useState<SymbolPick | null>(() => currentSymbol())
  const [rows, setRows] = useState<FinancialRow[]>([])
  const [msg, setMsg] = useState('')
  const req = useRef(0)

  const load = useCallback(async (code: string) => {
    const id = ++req.current
    setMsg('조회 중…')
    try {
      const r = await fetchFinancials(code)
      if (id !== req.current) return
      setRows(r.rows)
      setMsg(r.rows.length ? '' : '이 종목은 아직 백필되지 않았습니다.')
    } catch (e) {
      if (id === req.current) setMsg(e instanceof Error ? e.message : '조회 실패')
    }
  }, [])

  useEffect(
    () =>
      onSymbolPick((s) => {
        setSymbol(s)
        void load(s.code)
      }),
    [load],
  )

  useEffect(() => {
    if (symbol) void load(symbol.code)
  }, [])

  return (
    <div className="panel-col">
      <div className="toolbar">
        <span className="panel-title" style={{ margin: 0 }}>
          재무 {symbol ? `· ${symbol.name}` : ''}
        </span>
        <span className="badge" style={{ marginLeft: 'auto' }}>
          DART 연간
        </span>
      </div>
      <div className="panel-body">
        {!symbol && <p className="hint">종목을 선택하면 연간 재무가 표시됩니다.</p>}
        {msg && <p className="hint">{msg}</p>}
        {rows.length > 0 && (
          <table className="grid">
            <thead>
              <tr>
                <th>계정</th>
                {rows.map((r) => (
                  <th key={r.year} className="num">
                    {r.year}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {ACCOUNTS.map((acc) => (
                <tr key={acc}>
                  <td>{acc}</td>
                  {rows.map((r, i) => {
                    const v = r[acc]
                    const g = growth(v, rows[i + 1]?.[acc] ?? null)
                    return (
                      <td key={r.year} className="num" title={g == null ? '' : `전년比 ${g.toFixed(1)}%`}>
                        {v == null ? '-' : fmtEok(v)}
                        {g != null && (
                          <span className={g > 0 ? 'up' : g < 0 ? 'down' : 'flat'} style={{ marginLeft: 4 }}>
                            {g > 0 ? '+' : ''}
                            {g.toFixed(0)}%
                          </span>
                        )}
                      </td>
                    )
                  })}
                </tr>
              ))}
              <tr>
                <td className="flat">공시일</td>
                {rows.map((r) => (
                  <td key={r.year} className="num flat">
                    {r.disclosed ?? '-'}
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
        )}
        {rows.length > 0 && (
          <p className="hint">공시일 이후에만 쓸 수 있는 수치입니다 (백테스트 look-ahead 방지).</p>
        )}
      </div>
    </div>
  )
}
