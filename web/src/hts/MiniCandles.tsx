// 최근 [O,H,L,C] 배열을 작은 캔들로 그린다. 표시 전용 — 매매 판단에 쓰지 않는다.
type Props = {
  data: number[][] | undefined
  width?: number
  height?: number
}

const PAD = 1

export function MiniCandles({ data, width = 96, height = 36 }: Props) {
  if (!data || data.length === 0) {
    return <svg className="minicandles" width={width} height={height} aria-hidden />
  }

  const lo = Math.min(...data.map((c) => c[2]))
  const hi = Math.max(...data.map((c) => c[1]))
  const span = hi - lo || 1
  const step = (width - PAD * 2) / data.length
  const bw = Math.max(1, step * 0.6)
  const y = (v: number) => PAD + (height - PAD * 2) * (1 - (v - lo) / span)

  return (
    <svg className="minicandles" width={width} height={height} aria-hidden>
      {data.map(([o, h, l, c], i) => {
        const cx = PAD + step * (i + 0.5)
        const up = c >= o
        const color = up ? 'var(--hts-up)' : 'var(--hts-down)'
        const top = y(Math.max(o, c))
        const bh = Math.max(1, Math.abs(y(o) - y(c)))
        return (
          <g key={i}>
            <line x1={cx} x2={cx} y1={y(h)} y2={y(l)} stroke={color} strokeWidth="1" />
            <rect x={cx - bw / 2} y={top} width={bw} height={bh} fill={color} />
          </g>
        )
      })}
    </svg>
  )
}
