import { useId } from 'react'

// 장중 5분봉 라인 + 전일종가 기준선. 기준선 위/아래를 각각 상승색/하락색으로 칠한다.
type Props = {
  points: { t: string; v: number }[]
  prevClose: number | null
  width?: number
  height?: number
}

const PAD_Y = 6

export function IntradayChart({ points, prevClose, width = 300, height = 96 }: Props) {
  const gid = useId()
  if (points.length < 2) {
    return (
      <div style={{ height, display: 'flex', alignItems: 'center', color: 'var(--hts-text-3)', fontSize: 12 }}>
        장중 데이터 없음
      </div>
    )
  }

  const vals = points.map((p) => p.v)
  const base = prevClose ?? vals[0]
  const lo = Math.min(...vals, base)
  const hi = Math.max(...vals, base)
  const span = hi - lo || 1
  const x = (i: number) => (width * i) / (points.length - 1)
  const y = (v: number) => PAD_Y + (height - PAD_Y * 2) * (1 - (v - lo) / span)
  const yBase = y(base)

  const line = points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.v).toFixed(1)}`).join(' ')
  const area = `${line} L${width},${yBase.toFixed(1)} L0,${yBase.toFixed(1)} Z`
  const up = vals[vals.length - 1] >= base

  return (
    <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden>
      <defs>
        {/* 기준선 위쪽만 상승색, 아래쪽만 하락색으로 칠하도록 영역을 반씩 자른다 */}
        <clipPath id={`${gid}-up`}>
          <rect x="0" y="0" width={width} height={Math.max(0, yBase)} />
        </clipPath>
        <clipPath id={`${gid}-dn`}>
          <rect x="0" y={Math.max(0, yBase)} width={width} height={Math.max(0, height - yBase)} />
        </clipPath>
      </defs>
      <path d={area} fill="var(--hts-up)" opacity={0.14} clipPath={`url(#${gid}-up)`} />
      <path d={area} fill="var(--hts-down)" opacity={0.14} clipPath={`url(#${gid}-dn)`} />
      <line
        x1="0"
        x2={width}
        y1={yBase}
        y2={yBase}
        stroke="var(--hts-text-3)"
        strokeWidth={1}
        strokeDasharray="3 3"
        vectorEffect="non-scaling-stroke"
      />
      <path
        d={line}
        fill="none"
        stroke={up ? 'var(--hts-up)' : 'var(--hts-down)'}
        strokeWidth={1.6}
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  )
}
