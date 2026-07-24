import { useEffect, useMemo, useRef, useState } from 'react'
import {
  fetchConditions,
  runScreen,
  type ConditionCategory,
  type ConditionDef,
  type ScreenItem,
  type ScreenResponse,
} from '../../api'
import { notifyWatchlistChanged, pickSymbol, type SymbolPick } from '../bus'
import { chgClass, fmtChg, fmtEok, fmtPrice } from '../format'

// 조건검색 패널 — 키움 [0150] 조건검색 흐름.
// 좌측 카테고리 → 조건 선택 → 파라미터 입력 → [추가]로 A,B,C… 리스트 구성 → AND/OR 결합 검색.
// 임계값은 사용자가 매번 입력한다(서버 기본값 없음, CLAUDE.md). 이평·지표 계산은 전부 종가 기준.

type SavedCondition = { key: string; params: Record<string, number> }
type Preset = { logic: 'and' | 'or'; conditions: SavedCondition[] }
type Presets = Record<string, Preset>
type Watchlist = Record<string, SymbolPick[]> // WatchlistPanel 과 같은 저장 형식
type SortKey = 'chg' | 'amount' | 'marcap'

const PRESET_KEY = 'hts-screen-presets'
const WL_KEY = 'hts-watchlist'
const WL_GROUP = '조건검색'
const LIMIT = 100

// 지표 기간류 파라미터의 placeholder — 관례값 힌트일 뿐, 값은 항상 사용자가 입력한다.
const PLACEHOLDER: Record<string, string> = {
  short: '5',
  mid: '20',
  long: '60', // 정배열은 short<mid<long 이어야 하므로 힌트도 5/20/60 으로 유효하게
  period: '20',
  days: '20',
  within: '3',
}

function loadPresets(): Presets {
  try {
    return JSON.parse(localStorage.getItem(PRESET_KEY) ?? '{}') as Presets
  } catch {
    return {}
  }
}

/** 조건 행 라벨: A, B, C… (26개 초과는 번호) */
function rowLabel(i: number): string {
  return i < 26 ? String.fromCharCode(65 + i) : String(i + 1)
}

function fmtVal(v: number, unit: string): string {
  return `${v.toLocaleString()}${unit}`
}

/** "주가범위 1,000~50,000원" 식 요약 — min/max 쌍은 범위로, 나머지는 "라벨 값단위"로. */
function summarize(c: SavedCondition, def: ConditionDef | undefined): string {
  if (!def) return c.key // 카탈로그에 없는 key(구버전 조건식) — 서버가 400 으로 알려준다
  const parts: string[] = []
  for (const p of def.params) {
    if (p.key === 'min' || p.key === 'max') continue
    const v = c.params[p.key]
    if (v != null) parts.push(`${p.label} ${fmtVal(v, p.unit)}`)
  }
  const minDef = def.params.find((p) => p.key === 'min')
  const maxDef = def.params.find((p) => p.key === 'max')
  const min = c.params['min']
  const max = c.params['max']
  if (min != null && max != null && minDef && maxDef && minDef.unit === maxDef.unit) {
    parts.push(`${min.toLocaleString()}~${fmtVal(max, maxDef.unit)}`)
  } else {
    if (min != null && minDef) parts.push(`${fmtVal(min, minDef.unit)} ${minDef.label}`)
    if (max != null && maxDef) parts.push(`${fmtVal(max, maxDef.unit)} ${maxDef.label}`)
  }
  return parts.length ? `${def.name} ${parts.join(' · ')}` : def.name
}

export function ScreenPanel() {
  // ── 조건 카탈로그 (GET /api/conditions) ──
  const [catalog, setCatalog] = useState<ConditionCategory[]>([])
  const [catErr, setCatErr] = useState('')
  const [catReq, setCatReq] = useState(0) // 재시도 트리거

  useEffect(() => {
    let alive = true
    fetchConditions()
      .then((r) => {
        if (!alive) return
        setCatalog(r.categories)
        setOpenCat((prev) => prev ?? r.categories[0]?.key ?? null)
        setCatErr('')
      })
      .catch((e: unknown) => {
        if (alive) setCatErr(e instanceof Error ? e.message : '조건 목록 조회 실패')
      })
    return () => {
      alive = false
    }
  }, [catReq])

  const defMap = useMemo(() => {
    const m = new Map<string, ConditionDef>()
    for (const cat of catalog) for (const c of cat.conditions) m.set(c.key, c)
    return m
  }, [catalog])

  // ── 조건 선택·파라미터 입력 ──
  const [openCat, setOpenCat] = useState<string | null>(null)
  const [selKey, setSelKey] = useState<string | null>(null)
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [formErr, setFormErr] = useState('')
  const selDef = selKey ? defMap.get(selKey) : undefined

  function selectCond(c: ConditionDef) {
    setSelKey(c.key)
    setDraft({})
    setFormErr('')
  }

  // ── 조건 리스트(A, B, C…) + 결합 방식 ──
  const [added, setAdded] = useState<SavedCondition[]>([])
  const [logic, setLogic] = useState<'and' | 'or'>('and')

  function addCond() {
    if (!selDef) return
    const params: Record<string, number> = {}
    for (const p of selDef.params) {
      const raw = (draft[p.key] ?? '').trim()
      if (!raw) {
        if (p.required) {
          setFormErr(`[${p.label}] 값을 입력하세요.`)
          return
        }
        continue
      }
      const v = Number(raw)
      if (!Number.isFinite(v)) {
        setFormErr(`[${p.label}] 숫자를 입력하세요.`)
        return
      }
      if (p.type === 'int' && !Number.isInteger(v)) {
        setFormErr(`[${p.label}] 정수를 입력하세요.`)
        return
      }
      params[p.key] = v
    }
    if (Object.keys(params).length === 0) {
      setFormErr('값을 최소 1개 입력하세요.')
      return
    }
    setAdded((prev) => [...prev, { key: selDef.key, params }])
    setDraft({})
    setFormErr('')
  }

  function removeCond(i: number) {
    setAdded((prev) => prev.filter((_, idx) => idx !== i))
  }

  // ── 내 조건식 (localStorage 'hts-screen-presets') ──
  const [presets, setPresets] = useState<Presets>(loadPresets)
  const [presetName, setPresetName] = useState('')
  const [saving, setSaving] = useState(false) // 저장 이름 인라인 입력 토글 (prompt 창 금지)
  const [saveName, setSaveName] = useState('')
  const [confirmDel, setConfirmDel] = useState(false) // 삭제 2단계 확인 (confirm 창 금지)

  function persistPresets(next: Presets) {
    setPresets(next)
    localStorage.setItem(PRESET_KEY, JSON.stringify(next))
  }

  function loadPreset(name: string) {
    setPresetName(name)
    setConfirmDel(false)
    const p = presets[name]
    if (!p) return
    setAdded(p.conditions.map((c) => ({ key: c.key, params: { ...c.params } })))
    setLogic(p.logic)
  }

  function beginSave() {
    if (added.length === 0) {
      setFormErr('저장할 조건이 없습니다. 조건을 먼저 추가하세요.')
      return
    }
    setSaveName(presetName)
    setSaving(true)
  }

  function doSave() {
    const name = saveName.trim()
    if (!name) return
    persistPresets({ ...presets, [name]: { logic, conditions: added } })
    setPresetName(name)
    setSaving(false)
    setSaveName('')
  }

  function doDelete() {
    const next = { ...presets }
    delete next[presetName]
    persistPresets(next)
    setPresetName('')
    setConfirmDel(false)
  }

  // ── 검색 실행 (POST /api/screen/run) + 결과 ──
  const [date, setDate] = useState('')
  const [result, setResult] = useState<ScreenResponse | null>(null)
  const [runMsg, setRunMsg] = useState('')
  const [note, setNote] = useState('') // 관심종목 추가 등 부가 알림
  const [sortKey, setSortKey] = useState<SortKey | null>(null)
  const [sortDesc, setSortDesc] = useState(true)
  const [running, setRunning] = useState(false)
  // 연속 검색 시 늦게 도착한 이전 응답이 최신 결과를 덮지 않게 요청 순번으로 가드
  const runReq = useRef(0)

  async function run() {
    if (added.length === 0) {
      setRunMsg('조건을 1개 이상 추가한 뒤 검색하세요.')
      return
    }
    const req = ++runReq.current
    setRunning(true)
    setRunMsg('조회 중…')
    setNote('')
    try {
      const r = await runScreen({
        date: date || undefined,
        logic,
        conditions: added,
        limit: LIMIT,
      })
      if (req !== runReq.current) return // 더 새 검색이 이미 나갔다
      setResult(r)
      setRunMsg(
        r.items.length === 0
          ? `${r.date} 기준 조건에 맞는 종목이 없습니다.`
          : `${r.date} 기준 ${r.total}종목 (상위 ${r.items.length})`,
      )
    } catch (e) {
      if (req !== runReq.current) return
      setResult(null) // 에러 메시지 밑에 이전 결과가 남으면 조건과 결과가 어긋나 보인다
      setRunMsg(e instanceof Error ? e.message : '조회 실패')
    } finally {
      if (req === runReq.current) setRunning(false)
    }
  }

  function toggleSort(k: SortKey) {
    if (sortKey === k) {
      setSortDesc((d) => !d)
    } else {
      setSortKey(k)
      setSortDesc(true)
    }
  }

  // 클라이언트 정렬 — chg 는 null 가능(연초 첫 거래일 등)이라 null 은 항상 뒤로.
  const rows = useMemo(() => {
    const items = result?.items ?? []
    if (!sortKey) return items
    return [...items].sort((a, b) => {
      const av = a[sortKey]
      const bv = b[sortKey]
      if (av == null && bv == null) return 0
      if (av == null) return 1
      if (bv == null) return -1
      return sortDesc ? bv - av : av - bv
    })
  }, [result, sortKey, sortDesc])

  const arrow = (k: SortKey) => (sortKey === k ? (sortDesc ? ' ▼' : ' ▲') : '')

  // 결과 행 → 관심종목 '조건검색' 그룹에 추가 (WatchlistPanel 과 같은 저장 형식)
  function addWatch(it: ScreenItem) {
    let wl: Watchlist
    try {
      wl = JSON.parse(localStorage.getItem(WL_KEY) ?? '{}') as Watchlist
    } catch {
      wl = {}
    }
    const group = wl[WL_GROUP] ?? []
    wl[WL_GROUP] = [
      ...group.filter((g) => g.code !== it.code),
      { code: it.code, name: it.name, market: it.market },
    ]
    localStorage.setItem(WL_KEY, JSON.stringify(wl))
    notifyWatchlistChanged() // 열려 있는 관심종목 패널이 stale 상태로 덮어쓰지 않게
    setNote(`${it.name} → 관심종목 [${WL_GROUP}] 추가`)
  }

  return (
    <div className="panel-col">
      {/* 상단: 기준일 + 내 조건식 관리 */}
      <div className="toolbar">
        <input
          type="date"
          value={date}
          onChange={(e) => setDate(e.target.value)}
          title="기준일 (빈칸 = 최신 거래일)"
        />
        <select value={presetName} onChange={(e) => loadPreset(e.target.value)} title="저장된 조건식 불러오기">
          <option value="">내 조건식…</option>
          {Object.keys(presets).map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
        {saving ? (
          <>
            <input
              size={9}
              autoFocus
              placeholder="조건식 이름"
              value={saveName}
              onChange={(e) => setSaveName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && doSave()}
            />
            <button onClick={doSave}>확인</button>
            <button
              onClick={() => {
                setSaving(false)
                setSaveName('')
              }}
            >
              취소
            </button>
          </>
        ) : confirmDel ? (
          <>
            <button onClick={doDelete} title={`조건식 [${presetName}] 제거`}>
              정말 삭제
            </button>
            <button onClick={() => setConfirmDel(false)}>취소</button>
          </>
        ) : (
          <>
            <button onClick={beginSave} title="현재 조건 리스트를 조건식으로 저장">
              저장
            </button>
            <button disabled={!presetName} onClick={() => setConfirmDel(true)} title="선택한 조건식 삭제">
              삭제
            </button>
          </>
        )}
      </div>

      {/* 본문: 좌측 카테고리 트리 / 우측 폼·조건 리스트·결과 */}
      <div style={{ flex: 1, minHeight: 0, display: 'flex' }}>
        {/* 좌측: 조건 카테고리 목록 */}
        <div
          style={{
            flex: 'none',
            width: 180,
            overflowY: 'auto',
            borderRight: '1px solid var(--hts-border)',
            background: 'var(--hts-elev)',
            padding: '4px 0',
            boxSizing: 'border-box',
          }}
        >
          {catErr ? (
            <div style={{ padding: '0 8px' }}>
              <p className="hint">{catErr}</p>
              <button onClick={() => setCatReq((n) => n + 1)}>다시 시도</button>
            </div>
          ) : catalog.length === 0 ? (
            <p className="hint" style={{ padding: '0 8px' }}>
              조건 목록 불러오는 중…
            </p>
          ) : (
            catalog.map((cat) => (
              <div key={cat.key}>
                <div
                  onClick={() => setOpenCat((prev) => (prev === cat.key ? null : cat.key))}
                  style={{
                    padding: '5px 10px',
                    cursor: 'pointer',
                    fontWeight: 600,
                    fontSize: 12,
                    color: 'var(--hts-text)',
                    userSelect: 'none',
                  }}
                >
                  {openCat === cat.key ? '▾' : '▸'} {cat.name}
                </div>
                {openCat === cat.key &&
                  cat.conditions.map((c) => (
                    <div
                      key={c.key}
                      onClick={() => selectCond(c)}
                      title={c.desc}
                      style={{
                        padding: '4px 10px 4px 24px',
                        cursor: 'pointer',
                        fontSize: 12,
                        userSelect: 'none',
                        color: selKey === c.key ? 'var(--hts-accent)' : 'var(--hts-text-2)',
                        background: selKey === c.key ? 'var(--hts-active)' : undefined,
                      }}
                    >
                      {c.name}
                    </div>
                  ))}
              </div>
            ))
          )}
        </div>

        {/* 우측: 파라미터 폼 → 조건 리스트 → 결과 그리드 (이 영역만 스크롤) */}
        <div className="panel-body" style={{ minWidth: 0 }}>
          {/* 파라미터 입력 폼 */}
          {selDef ? (
            <>
              <p className="panel-title">{selDef.name}</p>
              <p className="hint">{selDef.desc} · 이평·지표는 종가 기준</p>
              <div className="form-row" style={{ flexWrap: 'wrap', alignItems: 'center' }}>
                {selDef.params.map((p) => (
                  <label
                    key={p.key}
                    style={{ display: 'flex', alignItems: 'center', gap: 3, fontSize: 11, color: 'var(--hts-text-2)' }}
                  >
                    {p.label}
                    <input
                      style={{ flex: 'none', width: 64 }}
                      placeholder={PLACEHOLDER[p.key] ?? ''}
                      value={draft[p.key] ?? ''}
                      onChange={(e) => setDraft({ ...draft, [p.key]: e.target.value })}
                      onKeyDown={(e) => e.key === 'Enter' && addCond()}
                    />
                    {p.unit}
                  </label>
                ))}
                <button onClick={addCond}>추가</button>
              </div>
            </>
          ) : (
            <p className="hint">왼쪽 카테고리에서 조건을 선택하면 파라미터 입력 폼이 표시됩니다.</p>
          )}
          {formErr && <p className="hint">{formErr}</p>}

          {/* 조건 리스트 + 결합 방식 + 검색 */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, margin: '10px 0 2px' }}>
            <span className="panel-title" style={{ margin: 0 }}>
              대상 조건 {added.length}건
            </span>
            <select value={logic} onChange={(e) => setLogic(e.target.value as 'and' | 'or')} title="조건 결합 방식">
              <option value="and">전체 AND</option>
              <option value="or">전체 OR</option>
            </select>
            <button className="primary" disabled={running} onClick={() => void run()}>
              {running ? '조회 중…' : '검색'}
            </button>
          </div>
          <p className="hint">결합은 전체 AND / 전체 OR 만 지원합니다 (괄호 조합 v1 미지원).</p>
          {added.length > 0 && (
            <table className="grid">
              <tbody>
                {added.map((c, i) => (
                  <tr key={`${c.key}-${i}`}>
                    <td className="flat" style={{ width: 22 }}>
                      {rowLabel(i)}
                    </td>
                    <td>{summarize(c, defMap.get(c.key))}</td>
                    <td className="num" style={{ width: 30 }}>
                      <button title="조건 삭제" onClick={() => removeCond(i)}>
                        ×
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {/* 결과 */}
          <p className="hint">
            {[runMsg || '조건을 추가하고 검색을 누르면 결과가 표시됩니다.', note].filter(Boolean).join(' · ')}
          </p>
          {rows.length > 0 && (
            <table className="grid">
              <thead>
                <tr>
                  <th>종목명</th>
                  <th className="num">현재가</th>
                  <th className="num" style={{ cursor: 'pointer' }} title="클릭 정렬" onClick={() => toggleSort('chg')}>
                    등락률{arrow('chg')}
                  </th>
                  <th className="num" style={{ cursor: 'pointer' }} title="클릭 정렬" onClick={() => toggleSort('amount')}>
                    거래대금{arrow('amount')}
                  </th>
                  <th className="num" style={{ cursor: 'pointer' }} title="클릭 정렬" onClick={() => toggleSort('marcap')}>
                    시총{arrow('marcap')}
                  </th>
                  <th className="num" />
                </tr>
              </thead>
              <tbody>
                {rows.map((it) => (
                  <tr key={it.code} onClick={() => pickSymbol({ code: it.code, name: it.name, market: it.market })}>
                    <td>{it.name}</td>
                    <td className={`num ${chgClass(it.chg)}`}>{fmtPrice(it.close)}</td>
                    <td className={`num ${chgClass(it.chg)}`}>{fmtChg(it.chg)}</td>
                    <td className="num">{fmtEok(it.amount)}</td>
                    <td className="num">{fmtEok(it.marcap)}</td>
                    <td className="num">
                      <button
                        title={`관심종목 [${WL_GROUP}] 그룹에 추가`}
                        onClick={(e) => {
                          e.stopPropagation()
                          addWatch(it)
                        }}
                      >
                        관심
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  )
}
