import { useId } from 'react'

// 최근 종가 배열을 선+면으로 그리는 미니차트. 표시 전용 — 매매 판단에 쓰지 않는다.
type Props = {
  data: number[] | undefined
  width?: number
  height?: number
  /** 마지막 값에 점을 찍는다 (카드용) */
  dot?: boolean
  /** 색 고정. 없으면 첫값 대비 마지막값으로 상승/하락 판정 */
  tone?: 'up' | 'down' | 'flat'
}

const PAD = 1.5

export function Sparkline({ data, width = 76, height = 24, dot = false, tone }: Props) {
  const gid = useId()
  if (!data || data.length < 2) {
    return <svg className="spark" width={width} height={height} aria-hidden />
  }

  const lo = Math.min(...data)
  const hi = Math.max(...data)
  const span = hi - lo || 1
  const stepX = (width - PAD * 2) / (data.length - 1)
  const y = (v: number) => PAD + (height - PAD * 2) * (1 - (v - lo) / span)
  const pts = data.map((v, i) => [PAD + i * stepX, y(v)] as const)

  const line = pts.map(([px, py], i) => `${i ? 'L' : 'M'}${px.toFixed(1)},${py.toFixed(1)}`).join(' ')
  const area = `${line} L${pts[pts.length - 1][0].toFixed(1)},${height} L${pts[0][0].toFixed(1)},${height} Z`
  const cls = tone ?? (data[data.length - 1] >= data[0] ? 'up' : 'down')

  return (
    <svg
      className={`spark ${cls}`}
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      aria-hidden
    >
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="currentColor" stopOpacity="0.18" />
          <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${gid})`} stroke="none" style={{ color: 'inherit' }} />
      <path d={line} fill="none" stroke="currentColor" strokeWidth={1.4} strokeLinejoin="round" strokeLinecap="round" />
      {dot && <circle cx={pts[pts.length - 1][0]} cy={pts[pts.length - 1][1]} r={2.2} fill="currentColor" />}
    </svg>
  )
}
