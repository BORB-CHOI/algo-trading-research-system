import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchFreshness, startHeavyUpdate, type DataFreshness as Fresh } from '../api'

// 헤더의 데이터 상태 알약. 누르면 소스별로 펼쳐진다.
//
// 왜 등급(색)을 쓰나: 묵은 데이터는 **화면이 멀쩡히 그려진다.** 차트도 나오고 숫자도
// 그럴듯하다. 날짜만 작게 띄우면 그냥 지나친다. 그래서 빨강·주황으로 걸리게 한다.
//
// 왜 "거래일 밀림"인가: 달력으로 세면 금요일 데이터를 놓고 월요일 아침마다 "사흘 전"이
// 되어 멀쩡한데 경고가 뜬다. 헛경고가 반복되면 진짜 경고도 안 보게 된다.

const POLL_MS = 60_000

const RANK: Record<string, number> = { ok: 0, warn: 1, stale: 2 }

const GRADE_WORD: Record<string, string> = {
  ok: '최신',
  warn: '조금 밀림',
  stale: '묵음',
}

function behindText(s: { last_date: string | null; days_behind: number | null }): string {
  if (!s.last_date) return '받은 적 없음'
  if (!s.days_behind) return '최신'
  return `${s.days_behind}거래일 밀림`
}

export function DataFreshness() {
  const [data, setData] = useState<Fresh | null>(null)
  const [open, setOpen] = useState(false)
  const [msg, setMsg] = useState('')
  const boxRef = useRef<HTMLDivElement>(null)

  const load = useCallback(async () => {
    try {
      setData(await fetchFreshness())
    } catch {
      /* 데이터 상태를 못 읽는 것으로 화면이 멈추면 안 된다 */
    }
  }, [])

  useEffect(() => {
    void load()
    const t = setInterval(() => void load(), POLL_MS)
    return () => clearInterval(t)
  }, [load])

  // 받아오는 동안엔 자주 확인한다. 데이터 받기는 수 분~수십 분이라 2초 주기 —
  // 창을 닫았다 열어도 서버가 계속 돌고 있으면 여기서 이어서 보인다(오너 요청 2026-08-22).
  // (`refreshing` 은 서버가 켜질 때 스스로 도는 가벼운 세기다 — 버튼과는 무관하다.)
  useEffect(() => {
    if (!data?.refreshing && !data?.heavy.running) return
    const ms = data.heavy.running ? 2000 : 1000
    const t = setInterval(() => void load(), ms)
    return () => clearInterval(t)
  }, [data?.refreshing, data?.heavy.running, load])

  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent) => {
      if (!boxRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', away)
    return () => document.removeEventListener('mousedown', away)
  }, [open])

  if (!data) return null

  // 알약에 적힌 날짜는 **차트 일봉**의 날짜다. 그러니 알약 색도 차트 일봉 등급이어야 한다 —
  // 차트가 최신인데 수급이 묵었다고 알약이 빨개지면 "차트가 묵었나?"로 잘못 읽힌다.
  // 다른 소스가 더 나쁘면 옆에 작은 점을 하나 더 붙여 "열어 보라"고만 알린다.
  const chart = data.sources.find((s) => s.key === 'marcap')
  const chartGrade = chart?.grade ?? 'stale'
  const others = RANK[data.worst] > RANK[chartGrade] ? data.worst : null

  // 버튼 하나로 끝낸다 — 무거운 갱신이 끝에 "어디까지 받았나"까지 다시 센다.
  // 날짜와 장 상태는 서버가 본다. 화면에서 고를 게 없다.
  async function onUpdate() {
    setMsg('')
    try {
      const r = await startHeavyUpdate()
      setMsg(r.message)
      await load()
    } catch (e) {
      setMsg(e instanceof Error ? e.message : '받아오지 못했습니다')
    }
  }

  return (
    <div className="fresh" ref={boxRef}>
      <button
        className={`fresh-pill g-${chartGrade}`}
        title={
          others
            ? '차트 일봉 날짜입니다. 다른 데이터가 묵었습니다 — 눌러서 확인하세요.'
            : '데이터가 어디까지 들어와 있는지 — 눌러서 자세히'
        }
        onClick={() => setOpen((v) => !v)}
      >
        <i className="dot" />
        시세 {chart?.last_date ?? '없음'}
        {others && <i className={`alarm g-${others}`} aria-label="다른 데이터가 묵었습니다" />}
        {(data.refreshing || data.heavy.running) && (
          <em className="spin">받아오는 중</em>
        )}
      </button>

      {open && (
        <div className="fresh-pop">
          <header>
            <b>데이터가 어디까지 들어와 있나</b>
            <span className={`tag g-${data.worst}`}>{GRADE_WORD[data.worst]}</span>
          </header>

          <ul className="fresh-list">
            {data.sources.map((s) => (
              <li key={s.key} className={`g-${s.grade}`}>
                <i className="dot" />
                <div className="who">
                  <b>{s.label}</b>
                  <small>{s.why}</small>
                </div>
                <div className="when">
                  <b>{s.last_date ?? '—'}</b>
                  <small>{behindText(s)}</small>
                </div>
              </li>
            ))}
          </ul>

          <div className="fresh-act">
            {/* 버튼은 **하나**다(오너 요청 2026-08-29). 전에는 "지금 갱신"과
                "나무 봉·수급·신용잔고 갱신" 둘이었는데, 무거운 쪽이 끝에 가벼운 쪽 일을
                그대로 한다(⑤ 어디까지 받았나 다시 세기). 둘을 놓으면 어느 걸 눌러야 하는지
                매번 고민하게 되고, 가벼운 쪽만 눌러 놓고 "갱신했는데 왜 그대로지"가 된다. */}
            <button
              className="primary"
              disabled={data.heavy.running}
              onClick={() => void onUpdate()}
            >
              {data.heavy.running ? '받아오는 중…' : '데이터 받아오기'}
            </button>
            <p className="hint">
              밀린 날짜만큼 따라잡습니다. <b>창을 닫아도 서버에서 계속 돕니다</b> — 다시 열면
              여기서 이어서 보입니다. 일봉·분봉·수급·VI·시장 자금을 한 번에 갱신합니다.
            </p>

            {data.heavy.running && (
              <div className="fresh-gauge">
                <div className="fresh-gauge-head">
                  <span>{data.heavy.phase || '시작하는 중'}</span>
                  <span className="mono">
                    {data.heavy.total > 0
                      ? `${data.heavy.done.toLocaleString()} / ${data.heavy.total.toLocaleString()}`
                      : '준비 중…'}
                  </span>
                </div>
                <div className="fresh-gauge-track">
                  <div
                    className="fresh-gauge-fill"
                    style={{
                      width:
                        data.heavy.total > 0
                          ? `${Math.min(100, (data.heavy.done / data.heavy.total) * 100)}%`
                          : '0%',
                    }}
                  />
                </div>
              </div>
            )}

            {!data.heavy.running && data.heavy.result?.error != null && (
              <p className="hint warn">지난번 오류: {data.heavy.result.error}</p>
            )}
            {!data.heavy.running && data.heavy.result?.skipped != null && (
              <p className="hint">지난번: {data.heavy.result.skipped}</p>
            )}

              <p className="hint">
                터미널에서 직접 돌리고 싶으면(같은 잠금을 봐서 서로 안 겹칩니다):
              </p>
              <code className="fresh-cmd">{data.manual_command}</code>
            </div>
            {msg && <p className="hint">{msg}</p>}
          </div>
      )}
    </div>
  )
}
