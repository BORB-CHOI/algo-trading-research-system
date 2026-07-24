import type { CSSProperties } from 'react'

// 뉴스(자리)·Finviz(새창 폴백) 패널 — 카드형 레이아웃.

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

export function NewsPanel() {
  return (
    <div className="panel-body">
      <div style={cardStyle}>
        <div style={cardTitleStyle}>
          뉴스
          <span className="badge">Phase 2 에서 소스 연결 예정</span>
        </div>
        <p className="hint">
          뉴스 데이터 소스는 미정이다 (지침: 뉴스·공시는 Backtest Phase 2 때 추가).
          소스가 정해지면 이 패널에 연결한다.
        </p>
      </div>
    </div>
  )
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
