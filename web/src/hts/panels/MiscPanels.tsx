import type { CSSProperties } from 'react'

// Finviz 새창 폴백 패널.

const cardStyle: CSSProperties = {
  background: 'var(--hts-elev)',
  border: '1px solid var(--hts-border)',
  borderRadius: 4,
  padding: '10px 12px',
}

const cardTitleStyle: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  fontSize: 13,
  fontWeight: 600,
  color: 'var(--hts-text)',
  marginBottom: 6,
}

export function FinvizPanel() {
  return (
    <div className="panel-body">
      <div style={cardStyle}>
        <div style={cardTitleStyle}>finviz 원본 맵</div>
        <p className="hint">
          finviz 는 iframe 임베드를 차단한다(X-Frame-Options: SAMEORIGIN) — 원본은 새창으로 연다.
          한국 시장은 시장맵 패널(우리 marcap 데이터)이 같은 화면을 제공한다.
        </p>
        <button onClick={() => window.open('https://finviz.com/map.ashx', '_blank', 'width=1400,height=900')}>
          finviz 맵 새창으로 열기
        </button>
      </div>
    </div>
  )
}
