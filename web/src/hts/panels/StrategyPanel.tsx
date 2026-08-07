import { useEffect, useMemo, useState } from 'react'
import {
  fetchConditions,
  type FinanceCoverage,
  fetchStrategies,
  type ConditionCategory,
  type ConditionDef,
  type StrategyDef,
} from '../../api'
import type { ComputedPrices } from './SplitStages'
import { loadScreens, type Screens } from './screenStore'
import { emptyDraft, loadStrategies, type Strategies, type StrategyDraft } from './strategyStore'
import { SIM_SYM } from './strategy/common'
import { ScreenStep } from './strategy/ScreenStep'
import { StrategyStep } from './strategy/StrategyStep'
import { SimStep } from './strategy/SimStep'
import { BacktestStep } from './strategy/BacktestStep'

// 전략 화면은 3단계다.
//  ① 종목선정 — 조건검색식을 여러 개 만들고 고친다 (저장소: hts-screens)
//  ② 매매전략 — 그중 하나를 골라 분할 매수/매도·주문조건을 붙인다 (저장소: hts-strategies)
//  ③ 시뮬레이션 — 대표 종목에 전략 1호(상승장 사이클+분할)를 돌려 전용 차트로 확인 (오너 지시:
//    종목 차트 오버레이 ❌, 이 탭에서 본다)
// 정량 값은 전부 이 화면에서 입력한다 (ADR-0009 — 전략 숫자 하드코딩 금지).
//
// 전략 1호 고정 정의는 strategyOne.ts 가 단일 정본이다 (오너: "캡슐화") —
// 화면 표시·서버 요청·저장 검증(strategyStore.toStrategy) 전부 그 모듈을 쓴다.
//
// 이 파일은 얇은 셸이다 (구조 리팩토링 2026-08-06, 오너 승인: "StrategyPanel 화면별 분할") —
// 스텝 탭과 **여러 스텝이 진짜로 공유하는 상태**(screens·saved·draft·name·카탈로그·computed)만
// 들고, 스텝별 화면·상태는 strategy/ 아래 ScreenStep·StrategyStep·SimStep·BacktestStep 에 있다.

type Step = 'screen' | 'strategy' | 'sim' | 'backtest'

export function StrategyPanel() {
  const [step, setStep] = useState<Step>('screen')

  // ── 카탈로그 ──
  const [condCats, setCondCats] = useState<ConditionCategory[]>([])
  const [stratCat, setStratCat] = useState<StrategyDef[]>([])
  const [finCov, setFinCov] = useState<FinanceCoverage | null>(null)
  const [catErr, setCatErr] = useState('')
  const [catReq, setCatReq] = useState(0)

  useEffect(() => {
    let alive = true
    Promise.all([fetchConditions(), fetchStrategies()])
      .then(([c, s]) => {
        if (!alive) return
        setCondCats(c.categories)
        setFinCov(c.finance_coverage ?? null)
        setStratCat(s)
        setCatErr('')
        // 첫 카테고리 기본 선택은 ScreenStep 이 condCats 변화를 보고 처리한다 (분할 전과 동일 동작).
      })
      .catch((e: unknown) => {
        if (alive) setCatErr(e instanceof Error ? e.message : '카탈로그 조회 실패')
      })
    return () => {
      alive = false
    }
  }, [catReq])

  const condMap = useMemo(() => {
    const m = new Map<string, ConditionDef>()
    for (const cat of condCats) for (const c of cat.conditions) m.set(c.key, c)
    return m
  }, [condCats])

  // ── 스텝 간 공유 상태 ──
  // screens: ①이 만들고 ②(검색식 붙이기)·④(유니버스)·탭 배지가 읽는다.
  // saved·name·draft: ②가 편집하고 ③(전략 선택·실행)·④(전략 값)가 읽는다.
  // computed: ③ 실행 결과의 차수별 목표가 — ②의 분할 카드가 회색으로 보여준다.
  const [screens, setScreens] = useState<Screens>(loadScreens)
  const [saved, setSaved] = useState<Strategies>(loadStrategies)
  const [name, setName] = useState('')
  const [draft, setDraft] = useState<StrategyDraft>(emptyDraft)
  const [computed, setComputed] = useState<ComputedPrices>({})

  const catErrNode = catErr ? (
    <p className="hint warn">
      {catErr} <button onClick={() => setCatReq((n) => n + 1)}>다시 시도</button>
    </p>
  ) : null

  return (
    <div className="panel-col">
      <div className="steps">
        {(
          [
            ['screen', '① 종목선정', `검색식 ${Object.keys(screens).length}`],
            ['strategy', '② 매매전략', `전략 ${Object.keys(saved).length}`],
            ['sim', '③ 시뮬레이션', `${SIM_SYM.name} 기준`],
            ['backtest', '④ 백테스팅', '전수 검사'],
          ] as const
        ).map(([k, label, badge]) => (
          <button key={k} className={step === k ? 'on' : ''} onClick={() => setStep(k)}>
            {label}
            <span className="badge">{badge}</span>
          </button>
        ))}
      </div>

      {/* 스텝 컴포넌트는 넷 다 **항상 마운트**하고 비활성이면 null 을 그린다 —
          분할 전 셸이 모든 상태를 들고 있던 것과 같은 지속성(탭을 오가도 편집 중 조건·
          시뮬 결과·백테스트 결과가 남는다). ProChart 는 ③ 활성일 때만 실제로 마운트되므로
          SimStep 의 재진입 효과(simResultRef 재적용)도 분할 전과 똑같이 동작한다. */}
      <ScreenStep
        active={step === 'screen'}
        catErrNode={catErrNode}
        condCats={condCats}
        finCov={finCov}
        condMap={condMap}
        screens={screens}
        setScreens={setScreens}
      />
      <StrategyStep
        active={step === 'strategy'}
        catErrNode={catErrNode}
        condMap={condMap}
        stratCat={stratCat}
        screens={screens}
        saved={saved}
        setSaved={setSaved}
        name={name}
        setName={setName}
        draft={draft}
        setDraft={setDraft}
        computed={computed}
        onGoScreen={() => setStep('screen')}
      />
      <SimStep
        active={step === 'sim'}
        catErrNode={catErrNode}
        saved={saved}
        name={name}
        setName={setName}
        draft={draft}
        setDraft={setDraft}
        setComputed={setComputed}
      />
      <BacktestStep active={step === 'backtest'} catErrNode={catErrNode} screens={screens} draft={draft} />
    </div>
  )
}
