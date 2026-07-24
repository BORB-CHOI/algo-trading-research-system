import { useEffect, useState } from 'react'
import { fetchStrategies } from '../../api'
import { pickStrategy } from '../bus'

// 전략 오버레이 패널 — 신호 계산은 전부 파이썬(layer3). 여기는 선택 UI 만.
export function StrategyPanel() {
  const [strategies, setStrategies] = useState<string[]>([])
  const [strategy, setStrategy] = useState('')

  useEffect(() => {
    fetchStrategies().then(setStrategies).catch(() => setStrategies([]))
  }, [])

  function apply(name: string) {
    setStrategy(name)
    pickStrategy(name) // 모든 차트 패널에 전파
  }

  return (
    <div className="panel-col">
      {/* 상단 툴바 — 제목 + 적용 상태 배지 */}
      <div className="toolbar">
        <span className="panel-title" style={{ margin: 0 }}>전략 오버레이</span>
        {strategy && (
          <span className="badge" style={{ marginLeft: 'auto' }}>
            적용중: {strategy}
          </span>
        )}
      </div>

      <div className="panel-body">
        <select value={strategy} onChange={(e) => apply(e.target.value)} style={{ width: '100%' }}>
          <option value="">(오버레이 없음)</option>
          {strategies.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <p className="hint">
          신호(▲매수/▼매도)는 파이썬이 계산한 시각화다. 예시 전략은 확정 전략이 아니다.
        </p>
      </div>
    </div>
  )
}
