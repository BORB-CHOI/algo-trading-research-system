import type { BuyStage, SellStage } from './strategyStore'
import { newBuyStage, newSellStage } from './strategyStore'
import { fmtPrice } from '../format'

// 분할 매수·매도 차수 편집기 (BORB-50).
//
// 오너 요구: "1차 2차 3차 분할 매수 ... 이게 아닐 때도 있으니 각각 다 커스텀으로
// 추가, 해제 하면서" — 그래서 차수가 고정 3개가 아니다. 추가·삭제·끄기가 전부 된다.
//
// 목표가는 여기서 입력받지 않는다. 되돌림 비율(매수)·반등률(매도)에서 서버가 계산하고,
// 계산 결과를 여기 회색으로 보여준다. 그 값이 마음에 안 들 때만 직접 덮어쓴다
// (priceOverride) — 자동값을 볼 수 있어야 덮어쓸지 판단할 수 있기 때문이다.

/** 서버가 계산해 준 차수별 목표가. 키는 stage.id. 아직 계산 전이면 비어 있다. */
export type ComputedPrices = Record<string, number>

function StageRow(props: {
  index: number
  enabled: boolean
  onToggle: (v: boolean) => void
  onRemove: () => void
  computed?: number
  override?: number
  onOverride: (v: number | undefined) => void
  children: React.ReactNode
}) {
  const auto = props.override == null
  // 목표가는 서버 계산 결과다. 계산 전에는 칸 자체를 안 보여준다 —
  // "계산 전" placeholder 가 필수 입력칸처럼 보인다는 오너 지적(2026-08-05).
  const hasPrice = props.computed != null || props.override != null
  return (
    <div className={`sstage ${props.enabled ? '' : 'off'}`}>
      <label className="chk" title={props.enabled ? '이 차수 끄기' : '이 차수 켜기'}>
        <input
          type="checkbox"
          checked={props.enabled}
          onChange={(e) => props.onToggle(e.target.checked)}
        />
        <b>{props.index + 1}차</b>
      </label>

      {props.children}

      {hasPrice && (
        <span className="px" title={auto ? '자동 계산된 목표가 — 고치면 그 값으로 주문선을 긋습니다' : '직접 지정한 가격'}>
          <span className="k">목표가</span>
          <input
            className={`amt ${auto ? 'auto' : ''}`}
            placeholder={props.computed == null ? '' : fmtPrice(props.computed)}
            value={props.override ?? ''}
            onChange={(e) => {
              const t = e.target.value.trim()
              props.onOverride(t === '' ? undefined : Number(t))
            }}
          />
          <span className="unit">원</span>
        </span>
      )}

      <button className="del" title="차수 삭제" onClick={props.onRemove}>
        ×
      </button>
    </div>
  )
}

/** 비중 합계 줄 — 비중은 절대 %(합 100 안)다 (오너 확정 2026-08-05).
 *  합이 100 을 넘으면 빨간 경고, 100 미만이면 남은 비중(미배분 = 현금 대기)을 보여준다. */
function WeightSum({ stages, of }: { stages: { enabled: boolean; weight: number }[]; of: string }) {
  const used = stages.filter((s) => s.enabled).reduce((a, s) => a + (s.weight || 0), 0)
  if (used <= 0) return null
  if (used > 100) {
    return <p className="wsum over">비중 합 {used}% — 100%를 넘을 수 없습니다</p>
  }
  const rest = used < 100 ? ` · 남은 ${100 - used}%는 ${of}` : ' — 전량 배분'
  return <p className="wsum">비중 합 {used}%{rest}</p>
}

function NumCell(props: {
  label: string
  value: number
  unit: string
  step?: string
  onChange: (v: number) => void
}) {
  return (
    <span className="cell">
      <span className="k">{props.label}</span>
      <input
        className="amt"
        inputMode="decimal"
        value={Number.isFinite(props.value) ? props.value : ''}
        onChange={(e) => props.onChange(Number(e.target.value))}
      />
      <span className="unit">{props.unit}</span>
    </span>
  )
}

export function BuyStages(props: {
  stages: BuyStage[]
  computed: ComputedPrices
  onChange: (next: BuyStage[]) => void
}) {
  const { stages, onChange } = props
  const patch = (id: string, p: Partial<BuyStage>) =>
    onChange(stages.map((s) => (s.id === id ? { ...s, ...p } : s)))

  return (
    <div className="sstages">
      {stages.length === 0 ? (
        <p className="empty-slot">
          분할 매수 차수가 없습니다.
          <br />
          되돌림 레벨마다 차수를 더해 보세요.
        </p>
      ) : (
        stages.map((s, i) => (
          <StageRow
            key={s.id}
            index={i}
            enabled={s.enabled}
            onToggle={(v) => patch(s.id, { enabled: v })}
            onRemove={() => onChange(stages.filter((x) => x.id !== s.id))}
            computed={props.computed[s.id]}
            override={s.priceOverride}
            onOverride={(v) => patch(s.id, { priceOverride: v })}
          >
            <NumCell
              label="되돌림"
              value={s.ratio * 100}
              unit="%"
              onChange={(v) => patch(s.id, { ratio: v / 100 })}
            />
            <NumCell
              label="비중"
              value={s.weight}
              unit="%"
              onChange={(v) => patch(s.id, { weight: v })}
            />
          </StageRow>
        ))
      )}
      <WeightSum stages={stages} of="매수 안 함(현금 대기)" />
      <div className="sstage-add">
        {/* 차트에 그려지는 피보 5선(FIB_RATIOS)과 같은 목록 — 화면마다 레벨이 다르면
            안 된다(오너 지적 2026-08-06). 0%·100%는 사이클 고점·저점 앵커라 제외.
            값은 추가된 뒤에도 자유롭게 고칠 수 있다 — 고정이 아니다. */}
        {[23.6, 38.2, 50, 61.8, 78.6].map((pct) => (
          <button
            key={pct}
            className="chip"
            onClick={() => onChange([...stages, newBuyStage(pct / 100, 0)])}
          >
            + {pct}%
          </button>
        ))}
        <button className="chip" onClick={() => onChange([...stages, newBuyStage(0, 0)])}>
          + 직접
        </button>
      </div>
    </div>
  )
}

export function SellStages(props: {
  stages: SellStage[]
  computed: ComputedPrices
  onChange: (next: SellStage[]) => void
}) {
  const { stages, onChange } = props
  const patch = (id: string, p: Partial<SellStage>) =>
    onChange(stages.map((s) => (s.id === id ? { ...s, ...p } : s)))

  return (
    <div className="sstages">
      {stages.length === 0 ? (
        <p className="empty-slot">
          분할 매도 차수가 없습니다.
          <br />
          매도를 안 걸면 <b>매수만 시뮬레이션</b>됩니다.
        </p>
      ) : (
        stages.map((s, i) => (
          <StageRow
            key={s.id}
            index={i}
            enabled={s.enabled}
            onToggle={(v) => patch(s.id, { enabled: v })}
            onRemove={() => onChange(stages.filter((x) => x.id !== s.id))}
            computed={props.computed[s.id]}
            override={s.priceOverride}
            onOverride={(v) => patch(s.id, { priceOverride: v })}
          >
            <NumCell
              label="반등"
              value={s.reboundPct}
              unit="%"
              onChange={(v) => patch(s.id, { reboundPct: v })}
            />
            <NumCell
              label="비중"
              value={s.weight}
              unit="%"
              onChange={(v) => patch(s.id, { weight: v })}
            />
          </StageRow>
        ))
      )}
      <WeightSum stages={stages} of="계속 보유" />
      <div className="sstage-add">
        <button className="chip" onClick={() => onChange([...stages, newSellStage(0, 0)])}>
          + 매도 차수
        </button>
      </div>
    </div>
  )
}
