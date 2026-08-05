// 장중 5분 캔들 + 전일종가 기준선. 캔들 색은 봉별 시가 대비 종가.
type Point = { t: string; o: number; h: number; l: number; c: number }

type Props = {
  points: Point[]
  prevClose: number | null
  width?: number
  height?: number
}

const PAD_Y = 6

export function IntradayChart({ points, prevClose, width = 300, height = 96 }: Props) {
  if (points.length < 2) {
    return (
      <div style={{ height, display: 'flex', alignItems: 'center', color: 'var(--hts-text-3)', fontSize: 12 }}>
        장중 데이터 없음
      </div>
    )
  }

  const base = prevClose ?? points[0].o
  const lo = Math.min(...points.map((p) => p.l), base)
  const hi = Math.max(...points.map((p) => p.h), base)
  const span = hi - lo || 1
  const step = width / points.length
  const bw = Math.max(1, step * 0.6)
  const y = (v: number) => PAD_Y + (height - PAD_Y * 2) * (1 - (v - lo) / span)

  return (
    <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden>
      <line
        x1="0"
        x2={width}
        y1={y(base)}
        y2={y(base)}
        stroke="var(--hts-text-3)"
        strokeWidth={1}
        strokeDasharray="3 3"
        vectorEffect="non-scaling-stroke"
      />
      {points.map((p, i) => {
        const cx = step * (i + 0.5)
        const color = p.c >= p.o ? 'var(--hts-up)' : 'var(--hts-down)'
        return (
          <g key={p.t}>
            <line x1={cx} x2={cx} y1={y(p.h)} y2={y(p.l)} stroke={color} strokeWidth="1" vectorEffect="non-scaling-stroke" />
            <rect
              x={cx - bw / 2}
              y={y(Math.max(p.o, p.c))}
              width={bw}
              height={Math.max(1, Math.abs(y(p.o) - y(p.c)))}
              fill={color}
            />
          </g>
        )
      })}
    </svg>
  )
}
