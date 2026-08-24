import { useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties, Dispatch, ReactNode, SetStateAction } from 'react'
import {
  runScreen,
  type ConditionCategory,
  type FinanceCoverage,
  type ConditionDef,
  type ScreenResponse,
  type TradeMarket,
} from '../../../api'
import { currentSymbol, onSymbolPick, pickSymbol, type SymbolPick } from '../../bus'
import { chgClass, fmtChg, fmtEok, fmtPrice } from '../../format'
import { MiniCandles } from '../../MiniCandles'
import { Card, Chip, Chips, KV, MsgLine } from '../../components/ui'
import { deleteScreen, saveScreen, type Screens } from '../screenStore'
import { parseParams, type SavedCondition } from '../strategyStore'
import { MarketNote, MarketPick, ParamInputs, rowLabel, summarizeCond, todayStr } from './common'

// ① 종목선정 — 조건검색식을 여러 개 만들고 고친다 (저장소: hts-screens).
// StrategyPanel.tsx 분할(구조 리팩토링 2026-08-06)로 옮겨온 스텝. 검색식 목록(screens)은
// ②·④·탭 배지가 같이 쓰므로 셸이 들고, 편집 중인 조건·검색 결과는 이 파일이 들고 있다.

const LIMIT = 100

function fmtMarket(m: string): string {
  if (m.startsWith('KOSPI')) return '코스피'
  if (m.startsWith('KOSDAQ')) return '코스닥'
  return m
}

export function ScreenStep(props: {
  active: boolean
  catErrNode: ReactNode
  condCats: ConditionCategory[]
  finCov: FinanceCoverage | null
  condMap: Map<string, ConditionDef>
  screens: Screens
  setScreens: Dispatch<SetStateAction<Screens>>
}) {
  const { active, catErrNode, condCats, finCov, condMap, screens, setScreens } = props

  const [sym, setSym] = useState<SymbolPick | null>(currentSymbol)
  useEffect(() => onSymbolPick(setSym), [])

  const [screenName, setScreenName] = useState('')
  const [logic, setLogic] = useState<'and' | 'or'>('and')
  const [conds, setConds] = useState<SavedCondition[]>([])
  const [catKey, setCatKey] = useState('')
  const [condKey, setCondKey] = useState('')
  const [condDraft, setCondDraft] = useState<Record<string, string>>({})
  const [screenErr, setScreenErr] = useState('')
  const [screenMsg, setScreenMsg] = useState('')
  const [namingScreen, setNamingScreen] = useState(false)
  const [screenNameDraft, setScreenNameDraft] = useState('')
  const [justAdded, setJustAdded] = useState<number | null>(null)

  // 카탈로그가 로드되면 첫 카테고리를 기본 선택 — 분할 전 셸의 fetch 성공 시
  // setCatKey 하던 동작을 그대로 옮긴 것 (이미 골라져 있으면 건드리지 않는다).
  useEffect(() => {
    setCatKey((prev) => prev || (condCats[0]?.key ?? ''))
  }, [condCats])

  const condDef = condMap.get(condKey)
  const catConds = condCats.find((c) => c.key === catKey)?.conditions ?? []

  // 조건마다 입력칸 개수가 달라 고를 때마다 아래가 위아래로 튀었다.
  // 가장 많은 조건 기준으로 자리를 미리 잡아 요동을 없앤다.
  const maxParams = useMemo(() => {
    const counts = condCats.flatMap((c) => c.conditions.map((x) => x.params.length))
    return counts.length ? Math.max(...counts) : 2
  }, [condCats])

  function pickCondition(key: string) {
    const def = condMap.get(key)
    if (!def) return
    for (const cat of condCats) {
      if (cat.conditions.some((c) => c.key === key)) setCatKey(cat.key)
    }
    setCondKey(key)
    setCondDraft({})
    setScreenErr('')
  }

  function addCond() {
    if (!condDef) {
      setScreenErr('조건을 선택하세요.')
      return
    }
    const r = parseParams(condDef.params, condDraft)
    if (!r.ok) {
      setScreenErr(r.error)
      return
    }
    setConds((cs) => {
      setJustAdded(cs.length) // 추가된 줄을 잠깐 강조 — 눌렀는지 아닌지 모르는 걸 막는다
      return [...cs, { key: condDef.key, params: r.value }]
    })
    setCondDraft({})
    setScreenErr('')
  }

  // 강조는 잠깐만. 남겨두면 "선택된 줄"로 오해된다.
  useEffect(() => {
    if (justAdded == null) return
    const t = window.setTimeout(() => setJustAdded(null), 1100)
    return () => window.clearTimeout(t)
  }, [justAdded])

  function loadScreen(name: string) {
    setScreenName(name)
    const s = screens[name]
    if (!s) return
    setLogic(s.logic)
    setConds(s.conditions.map((c) => ({ key: c.key, params: { ...c.params } })))
    setScreenMsg(`검색식 [${name}] 불러옴`)
  }

  function doSaveScreen() {
    const n = screenNameDraft.trim()
    if (!n) return
    setScreens(saveScreen(screens, n, { logic, conditions: conds }))
    setScreenName(n)
    setNamingScreen(false)
    setScreenMsg(`검색식 [${n}] 저장됨`)
  }

  function newScreen() {
    setScreenName('')
    setConds([])
    setLogic('and')
    setScreenMsg('새 검색식 — 조건을 추가하고 저장하세요.')
  }

  const [date, setDate] = useState(todayStr) // 기준일 기본 = 오늘 (오너 지시 2026-08-06)
  // 어느 거래소 체결로 거를지. 기본은 KRX — 지금까지와 결과가 같아야 한다(오너 2026-08-25).
  const [market, setMarket] = useState<TradeMarket>('krx')
  const [result, setResult] = useState<ScreenResponse | null>(null)
  const [runMsg, setRunMsg] = useState('')
  const [running, setRunning] = useState(false)
  const runReq = useRef(0)

  async function run(useConds: SavedCondition[], useLogic: 'and' | 'or') {
    const req = ++runReq.current
    setRunning(true)
    setRunMsg('조회 중…')
    try {
      const r = await runScreen({
        date: date || undefined,
        logic: useLogic,
        conditions: useConds,
        limit: LIMIT,
        market,
      })
      if (req !== runReq.current) return
      setResult(r)
      setRunMsg('')
    } catch (e) {
      if (req !== runReq.current) return
      setResult(null)
      setRunMsg(e instanceof Error ? e.message : '조회 실패')
    } finally {
      if (req === runReq.current) setRunning(false)
    }
  }

  // 비활성이어도 컴포넌트는 마운트를 유지한다(셸에서 항상 렌더) — 편집 중 조건·검색
  // 결과가 탭 이동에도 남는, 분할 전과 같은 지속성이다.
  if (!active) return null

  return (
    <>
      <div className="panel-body">
        {catErrNode}

        {/* ─────────────── ① 종목선정 ─────────────── */}
        <div className="split">
          {/* ── 왼쪽: 조건을 만드는 작업대 ── */}
          <div className="split-a">
            <Card title="조건 만들기" sub="카테고리 → 조건 → 값 입력">
              {/* 증권사 조건검색처럼 한 계층씩 — 카테고리 줄, 그 카테고리의 조건 줄.
                  빠른선택 칩 + 카테고리/조건 드롭다운의 이중 구조는 삭제 (오너 지적 2026-08-06). */}
              <Chips style={{ marginBottom: 8 }}>
                {condCats.map((c) => (
                  <Chip
                    key={c.key}
                    on={catKey === c.key}
                    onClick={() => {
                      setCatKey(c.key)
                      setCondKey('')
                      setCondDraft({})
                      setScreenErr('')
                    }}
                  >
                    {c.name}
                  </Chip>
                ))}
              </Chips>
              {catKey === 'finance' && finCov && (
                <MsgLine
                  warn
                  text={
                    finCov.ready
                      ? `재무 데이터는 ${finCov.codes.toLocaleString()}종목만 있습니다` +
                        `${finCov.years ? ` (${finCov.years[0]}~${finCov.years[1]}년)` : ''}` +
                        ' — 나머지 종목은 재무 조건에서 그냥 빠집니다.'
                      : '재무 데이터가 아직 없습니다 — 재무 조건을 걸면 아무 종목도 안 나옵니다.'
                  }
                />
              )}
              <Chips style={{ marginBottom: 10 }}>
                {catConds
                  .filter((c) => c.key !== 'new_low')
                  .map((c) => {
                    // 신고가/신저가는 증권사처럼 한 항목 — 구분은 고른 뒤 라디오로 (오너 지시).
                    const merged = c.key === 'new_high'
                    const on = merged
                      ? condKey === 'new_high' || condKey === 'new_low'
                      : condKey === c.key
                    return (
                      <Chip
                        key={c.key}
                        on={on}
                        title={c.desc}
                        onClick={() => {
                          if (!on) pickCondition(c.key)
                        }}
                      >
                        {merged ? '신고가/신저가' : c.name}
                      </Chip>
                    )
                  })}
                {catConds.length === 0 && <span className="hint">카테고리를 고르세요.</span>}
              </Chips>

              {/* 자리를 미리 잡아둔다 — 조건을 바꿔도 아래가 안 움직인다 */}
              <div className="paramzone" style={{ '--rows': maxParams } as CSSProperties}>
                {condDef ? (
                  <>
                    <p className="hint">
                      {condDef.desc}
                      {(condKey === 'new_high' || condKey === 'new_low') &&
                        ' · 52주 ≈ 250거래일 (기간+이내 ≤ 520)'}
                    </p>
                    {(condKey === 'new_high' || condKey === 'new_low') && (
                      <KV label="구분">
                        <span className="radios" style={{ marginLeft: 'auto' }}>
                          <label>
                            <input
                              type="radio"
                              checked={condKey === 'new_high'}
                              onChange={() => setCondKey('new_high')}
                            />
                            신고가
                          </label>
                          <label>
                            <input
                              type="radio"
                              checked={condKey === 'new_low'}
                              onChange={() => setCondKey('new_low')}
                            />
                            신저가
                          </label>
                        </span>
                      </KV>
                    )}
                    <ParamInputs
                      defs={condDef.params}
                      values={condDraft}
                      onChange={(k, v) => setCondDraft({ ...condDraft, [k]: v })}
                      onEnter={addCond}
                    />
                  </>
                ) : (
                  <p className="hint">위에서 조건을 고르면 입력할 값이 나옵니다.</p>
                )}
              </div>

              <div className="form-row" style={{ marginTop: 8 }}>
                <button className="primary" disabled={!condDef} onClick={addCond}>
                  검색식에 추가
                </button>
              </div>
              {/* 항상 한 줄을 차지한다 — 에러가 뜰 때만 생기면 화면이 튄다 */}
              <MsgLine text={screenErr} warn={!!screenErr} />
            </Card>

            {/* "종목 상세" 카드는 삭제 (오너 지적 2026-08-05) — 종목을 뜯어보는 자리는
                우측 종목 드로어·차트 탭이지 검색식 만드는 화면이 아니다. */}
          </div>

          {/* ── 오른쪽: 만들어진 검색식과 그 결과 ── */}
          <div className="split-b">
            <Card
              title="검색식"
              sub={screenName || '이름 없음 · 저장 안 됨'}
              right={
                <>
                  <span className={`badge ${conds.length ? 'on' : ''}`}>조건 {conds.length}</span>
                  <select value={screenName} onChange={(e) => loadScreen(e.target.value)} title="저장된 검색식">
                    <option value="">불러오기…</option>
                    {Object.keys(screens).map((n) => (
                      <option key={n} value={n}>
                        {n}
                      </option>
                    ))}
                  </select>
                </>
              }
            >
              {conds.length > 0 ? (
                <ol className="condlist">
                  {conds.map((c, i) => (
                    <li key={`${c.key}-${i}`} className={justAdded === i ? 'flash' : undefined}>
                      <span className="ix">{rowLabel(i)}</span>
                      <span className="tx">{summarizeCond(c, condMap.get(c.key))}</span>
                      <button
                        className="del"
                        title="조건 삭제"
                        onClick={() => setConds((cs) => cs.filter((_, idx) => idx !== i))}
                      >
                        ×
                      </button>
                    </li>
                  ))}
                </ol>
              ) : (
                <p className="empty-slot">
                  아직 조건이 없습니다.
                  <br />
                  이대로 검색하면 <b>전체 종목</b>이 나옵니다.
                </p>
              )}

              <div className="form-row" style={{ marginTop: 10 }}>
                <select
                  style={{ flex: 'none', width: 108 }}
                  value={logic}
                  onChange={(e) => setLogic(e.target.value as 'and' | 'or')}
                >
                  <option value="and">전체 AND</option>
                  <option value="or">전체 OR</option>
                </select>
                <input
                  type="date"
                  style={{ flex: 'none', width: 148 }}
                  value={date}
                  onChange={(e) => setDate(e.target.value)}
                  title="기준일 (기본 = 오늘, 휴장일이면 직전 거래일 기준)"
                />
                <MarketPick style={{ flex: 'none', width: 196 }} value={market} onChange={setMarket} />
                <button className="primary" disabled={running} onClick={() => void run(conds, logic)}>
                  {running ? '조회 중…' : '검색'}
                </button>
              </div>
              <MarketNote market={market} until={result?.unified_until} />
              <MsgLine text={screenMsg} />
            </Card>

            {result && (
              <Card title="검색 결과" flush sub={result.date}>
              <div className="sumcard">
                <b>{screenName || '이름 없는 검색식'}</b>
                <div className="pills">
                  <span>
                    검색 <b>{result.total.toLocaleString()}</b>종목
                  </span>
                  <span>
                    조건 <b>{result.conditions ?? conds.length}</b>개
                  </span>
                  <span>
                    수익률(당일){' '}
                    <b className={chgClass(result.avg_chg)}>{fmtChg(result.avg_chg)}</b>
                  </span>
                </div>
              </div>
              {result.items.length === 0 ? (
                <p className="hint" style={{ padding: '0 16px' }}>
                  조건에 맞는 종목이 없습니다.
                </p>
              ) : (
                <>
                  {result.themes_ready === false && (
                    <p className="hint" style={{ padding: '0 16px' }}>
                      테마 수집 중 — 잠시 후 다시 검색하면 표시됩니다.
                    </p>
                  )}
                  <ul className="hitlist">
                    {result.items.map((it) => (
                      <li
                        key={it.code}
                        className={sym?.code === it.code ? 'selected' : undefined}
                        onClick={() => pickSymbol({ code: it.code, name: it.name, market: it.market })}
                      >
                        <MiniCandles data={it.candles} />
                        <div className="who">
                          <span className="nm">{it.name}</span>
                          <small>
                            <span className={`mkt ${it.market.startsWith('KOSPI') ? 'kospi' : 'kosdaq'}`}>
                              {fmtMarket(it.market)}
                            </span>{' '}
                            {it.code}
                          </small>
                        </div>
                        <div className={`px num ${chgClass(it.chg)}`}>
                          <span className="close">{fmtPrice(it.close)}</span>
                          <small>{fmtChg(it.chg)}</small>
                        </div>
                        <div className="etc num">
                          <small>거래대금 {fmtEok(it.amount)}</small>
                          {it.themes && it.themes.length > 0 && (
                            <span className="themes" title={it.themes.join(' · ')}>
                              {it.themes.slice(0, 2).join(' · ')}
                              {it.themes.length > 2 && ` 외 ${it.themes.length - 2}`}
                            </span>
                          )}
                        </div>
                      </li>
                    ))}
                  </ul>
                </>
              )}
              </Card>
            )}
            {runMsg && <p className="hint warn">{runMsg}</p>}
          </div>
        </div>
      </div>

      {/* 단계별 하단 액션바 */}
      <div className="actionbar">
        <button onClick={newScreen}>새 검색식</button>
        {namingScreen ? (
          <>
            <input
              autoFocus
              placeholder="검색식 이름"
              value={screenNameDraft}
              onChange={(e) => setScreenNameDraft(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && doSaveScreen()}
            />
            <button className="cta" onClick={doSaveScreen}>
              확인
            </button>
          </>
        ) : (
          <>
            <button
              disabled={!screenName}
              onClick={() => {
                setScreens(deleteScreen(screens, screenName))
                newScreen()
                setScreenMsg(`검색식 [${screenName}] 삭제됨`)
              }}
            >
              삭제
            </button>
            <button
              className="cta"
              onClick={() => {
                setScreenNameDraft(screenName)
                setNamingScreen(true)
              }}
            >
              검색식 저장
            </button>
          </>
        )}
      </div>
    </>
  )
}
