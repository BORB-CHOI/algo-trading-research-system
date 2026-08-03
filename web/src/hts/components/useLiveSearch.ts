import { useEffect, useRef, useState } from 'react'
import { searchSymbols, type Symbol, type SymbolFilter } from '../../api'

// 한 글자마다 즉시 조회. 서버가 메모리 위 종목마스터를 훑을 뿐이라 디바운스가 없다.
// 늦게 온 응답이 최신 결과를 덮지 않게 요청 순번으로 막는다 — 검색 UI 는 전부 이 훅을 쓴다.
export function useLiveSearch(q: string, filter: SymbolFilter = {}, limit = 30) {
  const [hits, setHits] = useState<Symbol[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const req = useRef(0)
  const fkey = `${filter.market ?? ''}|${filter.kind ?? ''}`

  useEffect(() => {
    const term = q.trim()
    const id = ++req.current
    if (!term) {
      setHits([])
      setTotal(0)
      setError('')
      setLoading(false)
      return
    }
    setLoading(true)
    searchSymbols(term, filter, limit)
      .then((r) => {
        if (id !== req.current) return
        setHits(r.symbols)
        setTotal(r.total)
        setError('')
      })
      .catch((e: unknown) => {
        if (id !== req.current) return
        setHits([])
        setTotal(0)
        setError(e instanceof Error ? e.message : '검색 실패')
      })
      .finally(() => {
        if (id === req.current) setLoading(false)
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, fkey, limit])

  return { hits, total, loading, error }
}

/** ↑↓ 이동 + Enter 선택 — 검색 결과 목록 공통 키보드 처리 */
export function useListCursor(len: number) {
  const [cur, setCur] = useState(0)
  useEffect(() => setCur(0), [len])
  function onKeyDown(e: React.KeyboardEvent, onPick: (i: number) => void, onClose?: () => void) {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setCur((c) => Math.min(c + 1, len - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setCur((c) => Math.max(c - 1, 0))
    } else if (e.key === 'Enter' && len > 0) {
      onPick(cur)
    } else if (e.key === 'Escape') {
      onClose?.()
    }
  }
  return { cur, setCur, onKeyDown }
}
