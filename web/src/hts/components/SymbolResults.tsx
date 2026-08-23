import { motion } from 'motion/react'
import type { Symbol } from '../../api'

// 검색 결과 목록 — 헤더 드롭다운·검색 모달·관심종목이 전부 이걸 쓴다.
// 행 구성은 참고 화면과 동일: [종목명(검색어만 강조)] / [코드 · 시장 · 유형]

export function Highlight({ text, q }: { text: string; q: string }) {
  const i = q ? text.toLowerCase().indexOf(q.toLowerCase()) : -1
  if (i < 0) return <>{text}</>
  return (
    <>
      {text.slice(0, i)}
      <mark>{text.slice(i, i + q.length)}</mark>
      {text.slice(i + q.length)}
    </>
  )
}

export function SymbolResults(props: {
  hits: Symbol[]
  q: string
  cur: number
  onHover: (i: number) => void
  onPick: (s: Symbol) => void
  /** 우측에 붙일 추가 버튼 (관심종목 담기 등) */
  trailing?: (s: Symbol) => React.ReactNode
  empty?: React.ReactNode
}) {
  if (props.hits.length === 0) return <>{props.empty}</>
  return (
    <>
      {props.hits.map((s, i) => (
        <motion.div
          key={s.code}
          className={`sym-row ${i === props.cur ? 'cur' : ''}`}
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.14, delay: Math.min(i, 8) * 0.012 }}
          onMouseEnter={() => props.onHover(i)}
        >
          <button className="main" onClick={() => props.onPick(s)}>
            <span className="nm">
              <Highlight text={s.name} q={props.q} />
            </span>
            <span className="meta">
              <span>{s.code}</span>
              <span>{s.market}</span>
              {s.kindLabel && s.kind !== 'common' && <span className="kind">{s.kindLabel}</span>}
              {s.delisted && (
                <span className="gone" title={`${s.lastDate ?? ''} 까지 거래됐습니다`}>
                  상장폐지
                </span>
              )}
            </span>
          </button>
          {props.trailing?.(s)}
        </motion.div>
      ))}
    </>
  )
}
