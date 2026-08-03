import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchNews, type NewsItem } from '../../api'
import { currentSymbol, onSymbolPick, type SymbolPick } from '../bus'

const LIMIT = 30

export function NewsPanel() {
  const [symbol, setSymbol] = useState<SymbolPick | null>(() => currentSymbol())
  const [scoped, setScoped] = useState(true) // true = 이 종목, false = 전체 증시
  const [items, setItems] = useState<NewsItem[]>([])
  const [msg, setMsg] = useState('')
  const req = useRef(0)

  const load = useCallback(async (code?: string) => {
    const id = ++req.current
    setMsg('조회 중…')
    try {
      const list = await fetchNews(code, LIMIT)
      if (id !== req.current) return
      setItems(list)
      setMsg(list.length ? '' : '표시할 뉴스가 없습니다.')
    } catch (e) {
      if (id === req.current) setMsg(e instanceof Error ? e.message : '뉴스 조회 실패')
    }
  }, [])

  useEffect(
    () =>
      onSymbolPick((s) => {
        setSymbol(s)
        setScoped(true)
        void load(s.code)
      }),
    [load],
  )

  useEffect(() => {
    void load(scoped && symbol ? symbol.code : undefined)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scoped])

  const target = scoped && symbol ? symbol.code : undefined

  return (
    <div className="panel-col">
      <div className="toolbar">
        <span className="panel-title" style={{ margin: 0 }}>
          뉴스 {symbol ? `· ${symbol.name}` : ''}
        </span>
        <button
          onClick={() => setScoped(false)}
          className={scoped ? undefined : 'primary'}
          title="증시 전체 뉴스"
        >
          전체증시
        </button>
        <button
          onClick={() => setScoped(true)}
          className={scoped && symbol ? 'primary' : undefined}
          disabled={!symbol}
          title="선택된 종목 뉴스"
        >
          이 종목
        </button>
        <button style={{ marginLeft: 'auto' }} onClick={() => void load(target)} title="새로고침">
          ⟳
        </button>
      </div>
      <div className="panel-body plain">
        {!symbol && <p className="hint">종목을 선택하면 그 종목 뉴스로 좁혀 봅니다.</p>}
        {msg && <p className="hint">{msg}</p>}
        {items.length > 0 && (
          <table className="grid">
            <tbody>
              {items.map((n, i) => (
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
