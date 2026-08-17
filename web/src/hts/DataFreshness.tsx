import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchFreshness, refreshData, type DataFreshness as Fresh } from '../api'

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

  // 갱신이 도는 동안엔 자주 확인한다 — 몇 초짜리라 1분 주기로는 끝난 걸 못 본다.
  useEffect(() => {
    if (!data?.refreshing) return
    const t = setInterval(() => void load(), 1000)
    return () => clearInterval(t)
  }, [data?.refreshing, load])

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

  async function onRefresh() {
    setMsg('')
    try {
      const r = await refreshData()
      setMsg(r.message)
      await load()
    } catch (e) {
      setMsg(e instanceof Error ? e.message : '갱신 실패')
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
        {data.refreshing && <em className="spin">갱신 중</em>}
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
            <button className="primary" disabled={data.refreshing} onClick={() => void onRefresh()}>
              {data.refreshing ? '갱신 중…' : '지금 갱신 (약 30초)'}
            </button>

            {/* 파일 16,576개를 훑느라 30초쯤 걸린다 — 돌고는 있는지 보여야 한다. */}
            {data.refreshing && (
              <div className="fresh-gauge">
                <div className="fresh-gauge-head">
                  <span>{data.progress.phase || '시작하는 중'}</span>
                  <span className="mono">
                    {data.progress.total > 0
                      ? `${data.progress.done.toLocaleString()} / ${data.progress.total.toLocaleString()}`
                      : '준비 중…'}
                  </span>
                </div>
                <div className="fresh-gauge-track">
                  <div
                    className="fresh-gauge-fill"
                    style={{
                      width:
                        data.progress.total > 0
                          ? `${Math.min(100, (data.progress.done / data.progress.total) * 100)}%`
                          : '0%',
                    }}
                  />
                </div>
                <p className="hint">
                  창을 닫거나 딴 화면을 봐도 서버에서 계속 돕니다 — 돌아오면 이어서 보입니다.
                </p>
              </div>
            )}
            <p className="hint">
              차트 일봉은 서버를 켤 때마다 자동으로 최신이 됩니다. 수급·신용잔고처럼 종목마다
              따로 받아야 하는 것은 호출 한도가 커서 버튼으로 시작하지 않습니다 — 아래 명령을
              직접 돌리세요.
            </p>
            <code className="fresh-cmd">{data.manual_command}</code>
            {msg && <p className="hint">{msg}</p>}
          </div>
        </div>
      )}
    </div>
  )
}
