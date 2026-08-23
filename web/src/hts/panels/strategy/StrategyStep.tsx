import { useMemo, useState } from 'react'
import type { Dispatch, ReactNode, SetStateAction } from 'react'
import type { ConditionDef, StrategyDef } from '../../../api'
import { Card, KV } from '../../components/ui'
import { BuyStages, SellStages, type ComputedPrices } from '../SplitStages'
import { FIB_STOP_CHOICES, STRATEGY_ONE_WAVE, isFixedDefinition } from '../strategyOne'
import type { ScreenDef, Screens } from '../screenStore'
import {
  deleteStrategy,
  emptyDraft,
  saveStrategy,
  toDraft,
  toStrategy,
  type Strategies,
  type StrategyDraft,
} from '../strategyStore'
import { ParamInputs, SellBasisPicker, rowLabel, summarizeCond } from './common'

// ② 매매전략 — ①의 검색식 하나를 골라 분할 매수/매도·주문조건을 붙인다 (저장소: hts-strategies).
// StrategyPanel.tsx 분할(구조 리팩토링 2026-08-06)로 옮겨온 스텝. 편집 중 draft·이름·저장
// 목록은 ③④가 같이 쓰므로 셸이 들고, 저장/삭제 진행 상태(naming 등)는 이 파일이 들고 있다.

export function StrategyStep(props: {
  active: boolean
  catErrNode: ReactNode
  condMap: Map<string, ConditionDef>
  stratCat: StrategyDef[]
  screens: Screens
  saved: Strategies
  setSaved: Dispatch<SetStateAction<Strategies>>
  name: string
  setName: Dispatch<SetStateAction<string>>
  draft: StrategyDraft
  setDraft: Dispatch<SetStateAction<StrategyDraft>>
  computed: ComputedPrices
  onGoScreen: () => void
}) {
  const { active, catErrNode, condMap, stratCat, screens, saved, setSaved, name, setName, draft, setDraft, computed } = props

  const stratMap = useMemo(() => new Map(stratCat.map((s) => [s.key, s])), [stratCat])

  const [naming, setNaming] = useState(false)
  const [nameDraft, setNameDraft] = useState('')
  const [confirmDel, setConfirmDel] = useState(false)
  const [msg, setMsg] = useState('')
  const set = <K extends keyof StrategyDraft>(k: K, v: StrategyDraft[K]) =>
    setDraft((d) => ({ ...d, [k]: v }))

  const entryDef = stratMap.get(draft.entryKey)
  // 검색식이 정한 신고가 기간 — **보여주기 전용**이다. 실제로 쓰는 값은 서버가
  // 검색식에서 다시 꺼낸다(정본 하나). 어디서 온 값인지 안 보이면 또 두 갈래가 된다.
  const newHighDays = useMemo(() => {
    const days = draft.conditions
      .filter((c) => c.key === 'new_high' || c.key === 'new_high_burst')
      .map((c) => Number(c.params?.days))
      .filter((n) => Number.isFinite(n) && n > 0)
    return days.length ? Math.max(...days) : null
  }, [draft.conditions])

  /** ①에서 만든 검색식을 전략의 종목선정으로 끌어온다 */
  function attachScreen(n: string) {
    const s: ScreenDef | undefined = screens[n]
    if (!s) {
      setDraft((d) => ({ ...d, screenName: '', conditions: [], logic: 'and' }))
      return
    }
    setDraft((d) => ({
      ...d,
      screenName: n,
      logic: s.logic,
      conditions: s.conditions.map((c) => ({ key: c.key, params: { ...c.params } })),
    }))
    setMsg(`[${n}] 검색식을 전략에 붙였습니다 (조건 ${s.conditions.length}개).`)
  }

  function loadSaved(n: string) {
    setName(n)
    setConfirmDel(false)
    setNaming(false)
    const s = saved[n]
    if (!s) return
    setDraft(toDraft(s))
    setMsg(`전략 [${n}] 불러옴`)
  }

  function newStrategy() {
    setName('')
    setDraft(emptyDraft())
    setConfirmDel(false)
    setNaming(false)
    setMsg('새 전략 — 검색식을 고르고 값을 채우세요.')
  }

  function beginSave() {
    const r = toStrategy(draft, entryDef?.params ?? [])
    if (!r.ok) {
      setMsg(r.error)
      return
    }
    setNameDraft(name)
    setNaming(true)
    setMsg('')
  }

  function doSave() {
    const n = nameDraft.trim()
    if (!n) return
    const r = toStrategy(draft, entryDef?.params ?? [])
    if (!r.ok) {
      setMsg(r.error)
      return
    }
    setSaved(saveStrategy(saved, n, r.value))
    setName(n)
    setNaming(false)
    setMsg(`전략 [${n}] 저장됨`)
  }

  function doDelete() {
    setSaved(deleteStrategy(saved, name))
    setMsg(`전략 [${name}] 삭제됨`)
    setName('')
    setDraft(emptyDraft())
    setConfirmDel(false)
  }

  if (!active) return null

  return (
    <>
      <div className="panel-body">
        {catErrNode}

        {/* ─────────────── ② 매매전략 ─────────────── */}
        <Card
          title="종목선정"
          sub="①에서 만든 검색식 사용"
          right={<span className="badge">{draft.conditions.length}개 조건</span>}
        >
          <div className="form-row">
            <select
              style={{ flex: 1 }}
              value={draft.screenName ?? ''}
              onChange={(e) => attachScreen(e.target.value)}
            >
              <option value="">검색식 선택… (비우면 전체 종목)</option>
              {Object.keys(screens).map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
            <button onClick={props.onGoScreen}>검색식 만들기</button>
          </div>
          {draft.conditions.length === 0 ? (
            <p className="hint">
              검색식을 고르지 않으면 <b>전체 종목</b>이 대상이 됩니다(제외정책 적용 후).
            </p>
          ) : (
            <table className="grid">
              <tbody>
                {draft.conditions.map((c, i) => (
                  <tr key={`${c.key}-${i}`}>
                    <td className="flat" style={{ width: 22 }}>
                      {rowLabel(i)}
                    </td>
                    <td>{summarizeCond(c, condMap.get(c.key))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="hint">종목 확인은 ① 종목선정 탭에서 — 검색식을 고르고 검색하세요.</p>
        </Card>

        <Card title="진입 기법" sub="피보나치 등">
          <KV label="기법">
            <select
              style={{ flex: 1 }}
              value={draft.entryKey}
              onChange={(e) => {
                set('entryKey', e.target.value)
                set('entryParams', {})
              }}
            >
              <option value="">선택…</option>
              {stratCat.map((s) => (
                <option key={s.key} value={s.key}>
                  {s.name}
                </option>
              ))}
            </select>
          </KV>
          {entryDef ? (
            <>
              <p className="hint">{entryDef.desc}</p>
              {/* 전략 1호(피보나치)의 파동·지지저항 값은 고정 정의 — 입력칸을 안 보여준다
                  (오너 결정 2026-08-06: "시작점·고점은 자동으로 구하는 건데 입력값을 왜
                  내가 만지나"). 값은 ③에서 돌려보고, 정본은 STRATEGY_ONE_WAVE. */}
              {isFixedDefinition(entryDef.key) ? (
                <p className="hint">
                  파동 바닥·꼭대기·지지저항 전부 자동으로 잡는다. 시작점은 이번 상승장이 시작된
                  지점(좌우 {STRATEGY_ONE_WAVE.zzDepth / 2}봉으로 꼭대기·바닥을 찾고, 종가가 직전
                  꼭대기를 넘어선 때의 바닥). 지지저항은 <b>피보나치 선 위아래 밴드 안</b>에서만
                  찾고, 그 안의 라운드 가격이 주문가다. 값은 차트 패널의 전략에서 돌려본다.
                </p>
              ) : (
                <ParamInputs
                  defs={entryDef.params}
                  values={draft.entryParams}
                  onChange={(k, v) => set('entryParams', { ...draft.entryParams, [k]: v })}
                />
              )}
              {/* 피보나치 끝점은 **검색식이 정한다** — 고르는 칸이 없다(오너 결정 2026-08-22:
                  "그럼 피보나치 끝점 이런 필터도 없어져야 겠지?"). 검색식에 신고가 조건이
                  있으면 그 기간으로, 없으면 파동 바닥 이후 최고 고가로 잰다. */}
              {isFixedDefinition(entryDef.key) && (
                <>
                  <KV label="되돌림을 어디서 재나">
                    <span className="ro">
                      {newHighDays == null
                        ? '파동 바닥부터 그 뒤 가장 높았던 고가까지'
                        : `최근 ${newHighDays}거래일 중 가장 높았던 고가까지`}
                    </span>
                  </KV>
                  <p className="hint">
                    {newHighDays == null
                      ? '검색식에 신고가 조건이 없어서 파동 바닥 이후 최고 고가로 잽니다. ①에서 신고가 조건을 붙이면 그 기간으로 바뀝니다.'
                      : '①에서 고른 검색식이 정한 기간입니다. 검색식을 고치면 여기도 같이 바뀝니다.'}
                  </p>

                  {/* 매수 타점을 언제까지 기다릴지 — 모든 전략 공통(오너 결정 2026-08-22).
                      각 매매는 기준일 계획을 그대로 쓰고, 이 기간 안에 못 사면 접는다.
                      전에는 못 사면 주문이 파동을 따라 위로 밀려 올라가 계획보다 비싸게
                      샀고(실측 60.4%), 한 매매가 종목을 최장 787일 붙잡았다. */}
                  <KV label="매수를 언제까지 기다리나">
                    <input
                      type="number"
                      min={1}
                      style={{ width: 90 }}
                      value={draft.buyWaitDays}
                      onChange={(e) => set('buyWaitDays', e.target.value)}
                    />
                    <span className="unit">일</span>
                  </KV>
                  <p className="hint">
                    기준일에 건 값에 이 기간 안에 안 닿으면 <b>못 삼</b>으로 넘깁니다. 각 매매는
                    서로 별개라, 그 사이 검색식에 또 걸리면 그날 기준으로 새 매매가 열립니다.
                  </p>
                  {/* 한 기준일에 오른 구간(파동)은 여럿이다 (오너 2026-08-23).
                      그중 거래가 끊겨 이미 끝난 상승을 뺄지 정하는 값. 0 이면 전부 본다. */}
                  <KV label="거래가 한창때의">
                    <input
                      type="number"
                      min={0}
                      max={100}
                      step={5}
                      style={{ width: 90 }}
                      value={draft.waveCoolPct}
                      onChange={(e) => set('waveCoolPct', e.target.value)}
                    />
                    <span className="unit">% 까지 줄면 끝난 상승</span>
                  </KV>
                  <p className="hint">
                    한 종목이라도 <b>오른 구간은 여러 개가 겹쳐</b> 있습니다. 크게 오른 것 안에
                    작게 오른 것이 들어 있고, 바닥이 다르면 살 자리도 달라집니다.
                  </p>
                  <p className="hint">
                    20을 넣으면 <b>거래가 한창때의 20%까지 줄어든 구간</b>은 이미 끝난 상승으로
                    보고 뺍니다. 값이 <b>클수록 조금만 줄어도 끝난 것으로 봅니다.</b>
                    <b> 0을 넣으면 전부 봅니다.</b>
                  </p>
                </>
              )}
              {/* "차트에 적용" 버튼은 삭제 (오너 결정 2026-08-06) — ②는 설정만,
                  눈으로 확인은 ③ 시뮬레이션에서. 차트 탭 오버레이 입구도 함께 폐기. */}
            </>
          ) : (
            <p className="hint">기법을 선택하면 파라미터 입력 폼이 나옵니다.</p>
          )}
        </Card>

        <Card title="분할 매수" sub="되돌림 선에 가장 가까운 지지·저항 자리에 걸어 둡니다">
          <BuyStages stages={draft.buy} computed={computed} onChange={(b) => set('buy', b)} />
          <KV label="차수끼리 적어도" style={{ marginTop: 8 }}>
            <input
              className="amt"
              placeholder="10"
              value={draft.buyMinGapPct}
              onChange={(e) => set('buyMinGapPct', e.target.value)}
            />
            <span className="unit">% 는 벌어지게</span>
          </KV>
          <p className="hint">
            다음 차수는 앞 차수보다 이만큼 아래에 걸립니다. 0을 넣으면 안 씁니다. 안 쓰면
            200,000원과 220,000원처럼 9%밖에 안 벌어진 두 차수가 나올 수 있습니다.
          </p>
          <KV label="걸어 둔 선에서" style={{ marginTop: 8 }}>
            <input
              className="amt"
              placeholder="0"
              value={draft.buyTickOffset}
              onChange={(e) => set('buyTickOffset', e.target.value)}
            />
            <span className="unit">호가 옮겨서</span>
          </KV>
          <p className="hint">
            고른 지지·저항 자리에서 몇 호가 올려(+)/내려(−) 걸지 정합니다. 0이면 그 자리 그대로입니다.
          </p>
        </Card>

        <Card title="분할 매도" sub="무엇을 기준으로 몇 % 오르면 팔지 정합니다">
          <SellBasisPicker value={draft.sellBasis} onChange={(b) => set('sellBasis', b)} />
          <SellStages stages={draft.sell} computed={computed} onChange={(s) => set('sell', s)} />
          <KV label="걸어 둔 선에서" style={{ marginTop: 8 }}>
            <input
              className="amt"
              placeholder="0"
              value={draft.sellTickOffset}
              onChange={(e) => set('sellTickOffset', e.target.value)}
            />
            <span className="unit">호가 옮겨서</span>
          </KV>
          <p className="hint">
            팔 값 위쪽에서 가장 가까운 지지·저항 자리를 찾아, 거기서 몇 호가 옮겨 겁니다.
          </p>
        </Card>

        <Card title="손절" sub="평단에서 몇 % · 되돌림 선 · 지지·저항 자리 중에 고릅니다">
          <KV label="손절을">
            <span className="radios" style={{ marginLeft: 'auto' }}>
              <label>
                <input type="checkbox" checked={draft.stopEnabled} onChange={(e) => set('stopEnabled', e.target.checked)} />
                손절 건다
              </label>
            </span>
          </KV>
          {draft.stopEnabled && (
            <>
              <KV label="어떻게 정하나">
                <span className="radios" style={{ marginLeft: 'auto' }}>
                  <label>
                    <input type="radio" checked={draft.stopMode === 'pct'} onChange={() => set('stopMode', 'pct')} />
                    평단 대비 %
                  </label>
                  <label>
                    <input type="radio" checked={draft.stopMode === 'fib'} onChange={() => set('stopMode', 'fib')} />
                    되돌림 선
                  </label>
                  <label>
                    <input type="radio" checked={draft.stopMode === 'support'} onChange={() => set('stopMode', 'support')} />
                    지지저항 기준
                  </label>
                </span>
              </KV>
              {draft.stopMode === 'pct' ? (
                <KV label="평단에서">
                  <input className="amt" placeholder="3" value={draft.stopPct} onChange={(e) => set('stopPct', e.target.value)} />
                  <span className="unit">% 아래</span>
                </KV>
              ) : draft.stopMode === 'fib' ? (
                <>
                  <KV label="어느 선까지 밀리면">
                    <select
                      style={{ flex: 1 }}
                      value={draft.stopFibRatio}
                      onChange={(e) => set('stopFibRatio', e.target.value)}
                    >
                      {FIB_STOP_CHOICES.map((r, i) => (
                        <option key={r} value={String(r)}>
                          {(r * 100).toFixed(1)}% 선 ({i + 1}번째
                          {i === FIB_STOP_CHOICES.length - 1 ? ' · 기본' : ''})
                        </option>
                      ))}
                    </select>
                  </KV>
                  <KV label="그 선에서">
                    <input
                      className="amt"
                      placeholder="0"
                      value={draft.stopTicks}
                      onChange={(e) => set('stopTicks', e.target.value)}
                    />
                    <span className="unit">호가</span>
                  </KV>
                  <p className="hint">
                    올라간 구간을 되돌린 자리에서 자릅니다. 평단이 아니라 <b>파동</b>으로
                    정해지는 자리라, 몇 번에 나눠 샀든 손절선은 그대로입니다.
                    78.6% 선까지 밀렸다는 건 오른 폭을 거의 다 반납했다는 뜻입니다.
                  </p>
                </>
              ) : (
                <>
                  <KV label="어디를 기준으로">
                    <select
                      style={{ flex: 1 }}
                      value={draft.stopSource}
                      onChange={(e) => set('stopSource', e.target.value as 'cycle_low' | 'custom')}
                    >
                      <option value="cycle_low">파동 바닥</option>
                      <option value="custom">값을 직접 넣기</option>
                    </select>
                  </KV>
                  {draft.stopSource === 'custom' && (
                    <KV label="그 값은">
                      <input className="amt" value={draft.stopCustom} onChange={(e) => set('stopCustom', e.target.value)} />
                      <span className="unit">원</span>
                    </KV>
                  )}
                  <KV label="그 자리에서">
                    <input
                      className="amt"
                      placeholder="-2"
                      value={draft.stopTicks}
                      onChange={(e) => set('stopTicks', e.target.value)}
                    />
                    <span className="unit">호가 옮겨서</span>
                  </KV>
                  <p className="hint">
                    그 자리에서 몇 호가 내려(−)/올려(+) 걸지 정합니다. -2를 넣으면 두 칸 아래입니다.
                    호가 = 그 가격대의 최소 단위(2천원대 1원, 60만원대 1,000원)라 어느
                    가격대든 "두 칸 아래"로 뜻이 같습니다.
                  </p>
                </>
              )}
            </>
          )}
          <p className="hint">살 값·팔 값·손절선이 차트에 어떻게 찍히는지는 ③ 시뮬레이션에서 봅니다.</p>
        </Card>

        {/* "다 팔고 난 뒤(다시 매수)" 카드는 삭제했다 (오너 2026-08-10) — 전 기간 검사가
            이제 검색식에 걸린 날마다 무조건 라운드를 열므로(중복 허용) 켜고 끌 것이 없다. */}

        {/* "매수 주문조건" 카드는 삭제했다 (오너 지적 2026-08-05).
            — 지정가/시장가: 이 전략은 미리 걸어둔 지정가로 받는 방식이라 선택지 자체가 없다.
            — 주문수량: 분할 차수의 비중(%)과 역할이 겹쳤다. 손익 계산용 수량은 ③으로 이동.
            — 신용 구분: 주문 전송이 없는 지금 단계(CLAUDE.md 단계 6 이전)에는 무의미.
            실주문 조건은 모의투자 주문을 붙이는 새 ADR 때 다시 만든다. */}

        {msg && <p className="hint">{msg}</p>}
      </div>

      <div className="actionbar">
        <select value={name} onChange={(e) => loadSaved(e.target.value)} title="저장된 전략">
          <option value="">내 전략…</option>
          {Object.keys(saved).map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
        {naming ? (
          <>
            <input
              autoFocus
              placeholder="전략 이름"
              value={nameDraft}
              onChange={(e) => setNameDraft(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && doSave()}
            />
            <button className="cta" onClick={doSave}>
              확인
            </button>
          </>
        ) : confirmDel ? (
          <>
            <button onClick={doDelete}>정말 삭제</button>
            <button onClick={() => setConfirmDel(false)}>취소</button>
          </>
        ) : (
          <>
            <button onClick={newStrategy}>새 전략</button>
            <button disabled={!name} onClick={() => setConfirmDel(true)}>
              삭제
            </button>
            <button className="cta" onClick={beginSave}>
              전략 저장
            </button>
          </>
        )}
      </div>
    </>
  )
}
