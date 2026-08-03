import { useEffect, useRef, useState } from 'react'
import type { Symbol, SymbolKind } from '../../api'
import { Modal } from './Modal'
import { SymbolResults } from './SymbolResults'
import { useListCursor, useLiveSearch } from './useLiveSearch'

// 검색 설정이 붙은 가운데 모달. 헤더 드롭다운은 좁아서 필터를 못 얹는다 — 필터는 여기서.
// 선택지는 marcap 실측 기준이다: 시장 4종, 유형 4종. ETF/ETN 은 marcap 에 없어 넣지 않았다.

const MARKETS: { key: string; label: string }[] = [
  { key: '', label: '전체' },
  { key: 'KOSPI', label: '코스피' },
  { key: 'KOSDAQ', label: '코스닥' },
  { key: 'KONEX', label: '코넥스' },
]

const KINDS: { key: SymbolKind | ''; label: string }[] = [
  { key: '', label: '전체' },
  { key: 'common', label: '보통주' },
  { key: 'preferred', label: '우선주' },
  { key: 'spac', label: '스팩' },
  { key: 'reit', label: '리츠' },
]

export function SearchModal(props: {
  open: boolean
  onClose: () => void
  onPick: (s: Symbol) => void
  /** 헤더에서 치던 검색어를 그대로 이어받는다 */
  initialQuery?: string
  /** 결과 행 우측 버튼 (관심종목 담기 등) */
  trailing?: (s: Symbol) => React.ReactNode
}) {
  const [q, setQ] = useState('')
  const [market, setMarket] = useState('')
  const [kind, setKind] = useState<SymbolKind | ''>('')
  const { hits, total, loading, error } = useLiveSearch(q, { market, kind }, 60)
  const { cur, setCur, onKeyDown } = useListCursor(hits.length)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!props.open) return
    setQ(props.initialQuery ?? '')
    const t = setTimeout(() => inputRef.current?.focus(), 60)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.open])

  function pick(s: Symbol) {
    props.onPick(s)
    props.onClose()
  }

  const filtered = market || kind
  return (
    <Modal
      open={props.open}
      onClose={props.onClose}
      title="종목 검색"
      width={620}
      footer={
        <>
          <button
            onClick={() => {
              setMarket('')
              setKind('')
            }}
            disabled={!filtered}
          >
            초기화
          </button>
          <button className="cta" onClick={props.onClose}>
            확인
          </button>
        </>
      }
    >
      <div className="omni wide">
        <svg className="ico" width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden>
          <circle cx="7" cy="7" r="5" stroke="currentColor" strokeWidth="1.6" />
          <path d="M11 11l4 4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        </svg>
        <input
          ref={inputRef}
          value={q}
          placeholder="종목명 · 코드 — 한 글자만 쳐도 찾습니다"
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => onKeyDown(e, (i) => hits[i] && pick(hits[i]), props.onClose)}
        />
        {q && (
          <button className="clear" title="지우기" onClick={() => setQ('')}>
            ✕
          </button>
        )}
      </div>

      <p className="filter-label">시장</p>
      <div className="chips">
        {MARKETS.map((m) => (
          <button key={m.key} className={`chip ${market === m.key ? 'on' : ''}`} onClick={() => setMarket(m.key)}>
            {m.label}
          </button>
        ))}
      </div>
      <p className="filter-label">종목 유형</p>
      <div className="chips">
        {KINDS.map((k) => (
          <button key={k.key} className={`chip ${kind === k.key ? 'on' : ''}`} onClick={() => setKind(k.key)}>
            {k.label}
          </button>
        ))}
      </div>

      <div className="search-results">
        {error && <p className="hint warn">{error}</p>}
        {!error && q.trim() && (
          <p className="hint">
            {loading ? '찾는 중…' : `${total.toLocaleString()}건${total > hits.length ? ` (상위 ${hits.length})` : ''}`}
          </p>
        )}
        <SymbolResults
          hits={hits}
          q={q.trim()}
          cur={cur}
          onHover={setCur}
          onPick={pick}
          trailing={props.trailing}
          empty={
            q.trim() && !loading ? (
              <p className="empty">'{q.trim()}' 검색 결과가 없습니다.{filtered && ' 필터를 풀어보세요.'}</p>
            ) : (
              <p className="empty">종목명이나 코드를 입력하세요.</p>
            )
          }
        />
      </div>
    </Modal>
  )
}
