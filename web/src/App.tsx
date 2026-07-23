import { useEffect, useRef, useState } from 'react'
import { ProChart, type ProChartHandle } from './ProChart'
import { fetchScreen, fetchStrategies, type ScreenResponse } from './api'
import './App.css'

const EOK = 1e8 // 입력은 억원 단위, API 는 원 단위

function fmtEok(won: number): string {
  const eok = won / EOK
  return eok >= 10000 ? `${(eok / 10000).toFixed(1)}조` : `${Math.round(eok).toLocaleString()}억`
}

export default function App() {
  const chartRef = useRef<ProChartHandle>(null)

  // 조건검색 (BORB-39 ③) — 임계값은 사용자가 매번 입력. 확정값 아님.
  const [date, setDate] = useState('')
  const [minAmount, setMinAmount] = useState('')
  const [minMarcap, setMinMarcap] = useState('')
  const [maxMarcap, setMaxMarcap] = useState('')
  const [result, setResult] = useState<ScreenResponse | null>(null)
  const [screenError, setScreenError] = useState('')
  const [loading, setLoading] = useState(false)

  // 전략 오버레이 (BORB-39 ④) — 신호 계산은 전부 파이썬.
  const [strategies, setStrategies] = useState<string[]>([])
  const [strategy, setStrategy] = useState('')
  const [signalMsg, setSignalMsg] = useState('')

  useEffect(() => {
    fetchStrategies().then(setStrategies).catch(() => setStrategies([]))
  }, [])

  async function runScreen() {
    setLoading(true)
    setScreenError('')
    try {
      const num = (s: string) => (s.trim() ? Number(s) * EOK : undefined)
      setResult(
        await fetchScreen({
          date: date || undefined,
          minAmount: num(minAmount),
          minMarcap: num(minMarcap),
          maxMarcap: num(maxMarcap),
        }),
      )
    } catch (e) {
      setResult(null)
      setScreenError(e instanceof Error ? e.message : '조회 실패')
    } finally {
      setLoading(false)
    }
  }

  async function applyStrategy(name: string) {
    setStrategy(name)
    setSignalMsg('')
    try {
      const n = await chartRef.current?.applyStrategy(name || null)
      setSignalMsg(name ? `신호 ${n ?? 0}개 표시` : '')
    } catch (e) {
      setSignalMsg(e instanceof Error ? e.message : '신호 조회 실패')
    }
  }

  return (
    <div className="app">
      <header className="appbar">
        케이스 검사기 <span className="sub">— 탐색·가설용. 검증은 가드레일 백테스트 몫</span>
      </header>
      <div className="body">
        <aside className="sidebar">
          <section>
            <h2>조건검색</h2>
            <label>
              기준일 <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
            </label>
            <label>
              거래대금 ≥ (억)
              <input
                inputMode="numeric"
                placeholder="예: 100"
                value={minAmount}
                onChange={(e) => setMinAmount(e.target.value)}
              />
            </label>
            <label>
              시총 ≥ (억)
              <input
                inputMode="numeric"
                placeholder="예: 1000"
                value={minMarcap}
                onChange={(e) => setMinMarcap(e.target.value)}
              />
            </label>
            <label>
              시총 ≤ (억)
              <input
                inputMode="numeric"
                placeholder="예: 30000"
                value={maxMarcap}
                onChange={(e) => setMaxMarcap(e.target.value)}
              />
            </label>
            <button className="primary" onClick={runScreen} disabled={loading}>
              {loading ? '조회 중…' : '검색'}
            </button>
            {screenError && <p className="error">{screenError}</p>}
            {result && (
              <>
                <p className="hint">
                  {result.date} 기준 {result.total}종목 (거래대금순 상위 {result.items.length})
                </p>
                <ul className="screen-list">
                  {result.items.map((it) => (
                    <li key={it.code}>
                      <button
                        onClick={() => chartRef.current?.showSymbol(it.code, it.name, it.market)}
                      >
                        <span className="name">{it.name}</span>
                        <span className="meta">
                          {it.code} · 대금 {fmtEok(it.amount)} · 시총 {fmtEok(it.marcap)}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </section>
          <section>
            <h2>전략 오버레이</h2>
            <select value={strategy} onChange={(e) => applyStrategy(e.target.value)}>
              <option value="">(없음)</option>
              {strategies.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            {signalMsg && <p className="hint">{signalMsg}</p>}
            <p className="hint">신호는 파이썬이 계산한 시각화다. 예시 전략은 확정 전략이 아니다.</p>
          </section>
        </aside>
        <main className="chart-area">
          <ProChart ref={chartRef} />
        </main>
      </div>
    </div>
  )
}
