"""멀티종목 백테스트 러너 — 선별(조건검색식) → 전략 신호 → 종목별 체결 → 집계 (ADR-0007/0009).

ADR-0009 §2: **화면의 조건검색식이 곧 백테스트 유니버스다.** `run_universe` 는
POST /api/screen/run 과 동일한 데이터 형식(conditions 리스트 + logic)을 받아
layer3 `cond_registry` 로 평가한다 — 조건 정의·계산의 정본은 하나, 하드코딩 없음.
전략도 마찬가지로 `{key, params}` 데이터로 받아 layer3 전략 카탈로그에서 찾는다.
모든 정량 값(조건 임계값·전략 파라미터)은 호출자가 준다 — 이 모듈엔 전략 숫자가 없다.

## v1 단순화 (알려진 한계)

- **유니버스 선별은 split 시작 직전 거래일 1회.** 그 이후의 신규상장·조건 이탈/편입은
  반영되지 않는다 — point-in-time 재선별(주기적 리밸런스)은 후속 작업.
  (상폐 종목 자체는 marcap 에 남아 있어 선별에는 잡힌다 — survivorship bias 는
  데이터 차원에서 방지되고, 구간 중 상폐로 데이터가 끊기면 미청산 포지션은
  거래로 세지 않는다(backtest.run_symbol, 보수적).)
- **cum_net_return 은 전 종목 거래를 순차 복리한 값** — 자본 배분·동시 보유·포지션
  크기를 반영하지 않는다(곱은 순서 무관). 포트폴리오 자본곡선은 후속.
- 전략 함수에는 split 종료일까지의 데이터만 준다(그 이후 미래는 구조적으로 차단).
  split 구간 **안**의 인과성(신호일까지의 데이터만 사용)은 전략 함수 책임이다 —
  run_symbol docstring 의 신뢰 경계와 동일.
"""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from src.layer1_data.daily import daily_bars
from src.layer1_data.exclusions import DEFAULT_POLICY, ExclusionPolicy, apply_exclusions
from src.layer1_data.marcap_loader import available_years, load_years
from src.layer3_strategy import conditions as cond_registry
from src.layer4_execution.backtest import (
    MIN_RELIABLE_TRADES,
    Trade,
    resolve_period,
    run_symbol,
    slice_period,
)
from src.layer4_execution.costs import DEFAULT_COST, CostModel
from src.layer4_execution.slippage import SqrtImpactSlippage

# 전략 신호 함수 시그니처(카탈로그 인터페이스): (일봉 df, **params) → 신호 행 DataFrame.
# 신호 행: Date + side('buy'|'sell'). df 의 실제 거래일에만 신호를 낸다.
StrategyFn = Callable[..., pd.DataFrame]


def _resolve_strategy(key: str) -> StrategyFn:
    """전략 카탈로그 접점 — **카탈로그 임포트는 이 함수 한 곳뿐이다.**

    case_overlay 가 카탈로그 방식(ADR-0009: 이름·설명·파라미터 스키마)으로 병렬 개편
    중이므로, 여기서는 "카탈로그에서 key 로 신호 함수를 찾아 params 를 키워드 인자로
    넘긴다"는 인터페이스만 가정한다. 개편 후 모듈/변수 이름이 바뀌면 이 본문만 고친다.
    """
    from src.layer3_strategy.case_overlay import STRATEGIES  # 지연 임포트 — 개편 대비 격리

    fn = STRATEGIES.get(key)
    if fn is None:
        raise ValueError(f"전략 카탈로그에 없는 key: {key!r} (등록된 전략: {sorted(STRATEGIES)})")
    return fn


def signals_to_position(df: pd.DataFrame, signals: pd.DataFrame) -> pd.Series:
    """신호 행(Date, side) → 0/1 목표 포지션 열 (df 와 같은 길이·인덱스).

    buy 가 선 날부터 1, sell 이 선 날부터 0, 첫 신호 전은 0(미보유).
    포지션은 신호일(t)에 서기만 하고, 체결은 엔진(run_symbol)이 t+1 이후 첫 거래
    가능일 시가로 미룬다 — "신호 계산 시점 < 체결 시점" 불변식은 엔진이 강제한다.

    신호 날짜가 df 에 없는 거래일이면 ValueError — 조용히 버리면 sell 이 증발해
    포지션이 영원히 열려 있는 식의 무결성 사고가 나므로 즉시 실패시킨다.
    """
    if signals.empty:
        return pd.Series(0, index=df.index)
    if signals.duplicated("Date").any():
        raise ValueError("같은 날짜에 신호가 2개 이상 — 전략 함수 출력을 확인하세요.")
    missing = ~signals["Date"].isin(df["Date"])
    if missing.any():
        bad = signals.loc[missing, "Date"].iloc[0]
        raise ValueError(f"전략 신호 날짜 {bad.date()} 가 일봉에 없습니다 — 전략 함수 출력 확인.")
    want = signals.set_index("Date")["side"].map({"buy": 1, "sell": 0})
    if want.isna().any():
        raise ValueError("side 는 'buy' 또는 'sell' 이어야 합니다.")
    aligned = want.reindex(df["Date"]).ffill().fillna(0).astype(int)
    return pd.Series(aligned.to_numpy(), index=df.index)


def _load_selection_panel(split_start: pd.Timestamp, lookback: int) -> pd.DataFrame:
    """기본 데이터 소스: marcap 에서 선별용 일봉 패널(long 형)을 읽는다.

    split 시작 **이전** 데이터만, 기준일 + 룩백을 덮을 만큼 연도를 거슬러 로드한다.
    (연간 거래일 ~242일 — 룩백이 크면 전년도 하나로 모자랄 수 있다.)
    """
    years = available_years()
    if not years:
        raise FileNotFoundError("marcap 데이터가 없습니다 — data/marcap/data 확인.")
    frames: list[pd.DataFrame] = []
    n_dates = 0
    for y in range(min(split_start.year, years[-1]), years[0] - 1, -1):
        if y not in years:
            continue
        df = load_years(y, y)
        df = df[df["Date"] < split_start]
        if df.empty:
            continue
        frames.append(df)
        n_dates += df["Date"].nunique()
        if n_dates >= lookback + 1:
            break
    if not frames:
        raise ValueError(f"{split_start.date()} 이전 거래일 데이터가 없습니다.")
    return pd.concat(frames, ignore_index=True)


def _select_universe(
    conditions: list[dict],
    logic: str,
    split_start: pd.Timestamp,
    hist: pd.DataFrame | None,
    exclusions: ExclusionPolicy | None,
) -> tuple[list[str], pd.Timestamp]:
    """조건검색식으로 **검사할 종목**을 뽑는다. 기준일 = split 시작 직전 거래일 (1회).

    반환: (선별 종목코드 목록(코드 오름차순 — 재현성), 기준일, 코드→종목명).
    종목명은 화면 표시용이다 — 코드만 보여 주면 어느 회사인지 알 수 없다(오너 2026-08-09).
    """
    parsed = cond_registry.parse_conditions(conditions)  # 형식·룩백 검증 포함 (정본 재사용)
    lookback = cond_registry.required_lookback(parsed)

    if hist is None:
        hist = _load_selection_panel(split_start, lookback)
    else:
        hist = hist[hist["Date"] < split_start]  # look-ahead 금지: split 안쪽 데이터로 선별 ❌
    if hist.empty:
        raise ValueError(f"{split_start.date()} 이전 거래일 데이터가 없습니다.")

    base_date = hist["Date"].max()
    base = hist[hist["Date"] == base_date]
    if exclusions is not None:
        base = apply_exclusions(base, exclusions)  # 스팩·KONEX·우선주·리츠·관리종목 (ADR-0003)
    base = base.set_index("Code")

    # 룩백 만큼만 남긴 패널 — HistPanel 이 기준일 이후를 한 번 더 자른다(이중 가드).
    keep = hist["Date"].drop_duplicates().sort_values().iloc[-(lookback + 1) :]
    panel = cond_registry.HistPanel(hist[hist["Date"].isin(keep)], base_date)

    mask = cond_registry.evaluate(parsed, panel, base, logic)
    picked = base.index[mask]
    codes = sorted(str(c) for c in picked)
    names = (
        {str(c): str(n) for c, n in base.loc[picked, "Name"].items()}
        if "Name" in base.columns
        else {}
    )
    return codes, base_date, names


def aggregate_returns(rets: list[float]) -> dict:
    """순수익률 목록 → 집계 지표. **정의는 여기 한 곳뿐이다.**

    보관함에서 꺼낸 결과(거래 객체가 아니라 숫자만 남아 있다)도 같은 정의로 세야
    화면에 뜨는 숫자가 방금 돌린 것과 어긋나지 않는다.
    """
    n = len(rets)
    if n == 0:
        return {
            "n_trades": 0,
            "win_rate": None,
            "avg_win": None,
            "avg_loss": None,
            "expectancy": None,
            "cum_net_return": 0.0,
            "reliable": False,
        }
    s = pd.Series(rets)
    wins, losses = s[s > 0], s[s <= 0]
    return {
        "n_trades": n,
        "win_rate": float((s > 0).mean()),
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "expectancy": float(s.mean()),
        "cum_net_return": float((1 + s).prod() - 1),
        "reliable": n >= MIN_RELIABLE_TRADES,
    }


def _aggregate(trades: list[Trade]) -> dict:
    """전 종목 거래를 하나로 모은 지표. 정의:

    - win_rate: net_return > 0 비율. avg_win/avg_loss: 이익/손실(본전 이하 포함) 쪽 평균.
      한쪽이 비면 0.0 — expectancy = win_rate·avg_win + (1−win_rate)·avg_loss 가
      항상 평균 순수익률과 일치하게 유지한다.
    - cum_net_return: Π(1+r)−1. 순차 복리 가정(자본 배분 무시 — 모듈 docstring 한계).
    - reliable: N ≥ 30 (CLAUDE.md: N<30 신뢰 불가).
    """
    return aggregate_returns([t.net_return for t in trades])


def run_universe(
    conditions: list[dict],
    logic: str,
    strategy: dict,
    *,
    start: str | None = None,
    end: str | None = None,
    cost: CostModel = DEFAULT_COST,
    slippage: SqrtImpactSlippage | None = None,
    order_notional: float | None = None,
    exclusions: ExclusionPolicy | None = DEFAULT_POLICY,
    hist: pd.DataFrame | None = None,
    loader: Callable[[str], pd.DataFrame | None] = daily_bars,
) -> dict:
    """조건검색식으로 유니버스를 뽑아 종목별 백테스트를 돌리고 집계한다.

    인자 (전부 데이터 — ADR-0009):
    - conditions/logic: POST /api/screen/run 과 **동일한 형식**.
      예: [{"key": "price_range", "params": {"min": 1000}}], logic="and".
      빈 목록은 오류(조건검색과 같은 계약) — 전 종목이 필요하면 느슨한 조건을 명시한다.
    - strategy: {"key": 카탈로그 key, "params": {...}} — 신호 함수는 카탈로그에서 찾고
      params 를 키워드 인자로 넘긴다(_resolve_strategy 한 곳에서만 임포트).
    - start/end: 검사 구간. 화면에서 고른 날짜가 그대로 온다(ADR-0019).
      안 주면 2007-01-01 ~ 오늘. 코드가 구간을 나누거나 막지 않는다.
    - cost: ADR-0004 왕복 정액률. slippage + order_notional: 제곱근 충격 슬리피지(옵션).
    - exclusions: ADR-0003 유니버스 제외. Name/Market 컬럼이 없는 합성 데이터는 None.
    - hist/loader: 데이터 주입점(테스트용). 기본은 marcap(선별)과
      layer1.daily.daily_bars(상장 종목=나무 수집본 · 상폐=marcap 보정본).

    반환:
    {split, split_start, split_end, base_date, universe, skipped: {code: 사유},
     per_symbol: {code: run_symbol summary},
     metrics: {n_trades, win_rate, avg_win, avg_loss, expectancy, cum_net_return, reliable}}

    v1 한계(모듈 docstring 상세): 선별은 구간 시작 직전 거래일 1회 — 당시 기준 종목 목록으로
    재선별은 후속. cum_net_return 은 자본 배분 없는 순차 복리.
    """
    if logic not in ("and", "or"):
        raise ValueError(f"logic 은 'and' 또는 'or' 여야 합니다: {logic!r}")
    if not isinstance(strategy, dict) or "key" not in strategy:
        raise ValueError('strategy 는 {"key": ..., "params": {...}} 형식이어야 합니다.')
    strategy_fn = _resolve_strategy(str(strategy["key"]))
    params = strategy.get("params") or {}
    if not isinstance(params, dict):
        raise ValueError("strategy.params 는 dict 여야 합니다.")

    split_start, split_end = resolve_period(start, end)
    universe, base_date, _ = _select_universe(conditions, logic, split_start, hist, exclusions)

    per_symbol: dict[str, dict] = {}
    skipped: dict[str, str] = {}
    all_trades: list[Trade] = []
    for code in universe:
        raw = loader(code)
        if raw is None or raw.empty:
            skipped[code] = "데이터 없음"
            continue
        df = raw.sort_values("Date").reset_index(drop=True)
        if "Code" not in df.columns:  # 파생 parquet 은 Code 를 파일명으로만 든다
            df["Code"] = code
        # 전략에는 split 종료일까지만 준다 — split 이후 미래는 구조적으로 차단.
        # 시작 전 이력은 워밍업(이평 등)에 쓰라고 남긴다(과거 데이터 — look-ahead 아님).
        df = df[df["Date"] <= split_end]
        sliced = slice_period(df, split_start, split_end)
        if len(sliced) < 2:  # 체결은 신호 다음 날 — 최소 2 거래일 필요
            skipped[code] = "구간 내 거래일 부족"
            continue
        # split 이전에 선 buy 가 아직 유효하면 구간 첫날 신호로 이월된다(보유 중 진입).
        position = signals_to_position(df, strategy_fn(df, **params)).loc[sliced.index]
        result = run_symbol(
            sliced, position, cost=cost, slippage=slippage, order_notional=order_notional
        )
        per_symbol[code] = result.summary()
        all_trades.extend(result.trades)

    return {
        # 검사 구간 — 화면에서 고른 날짜 그대로 (ADR-0019). 'split' 이라는 이름은
        # 옛 보관함 기록(run_store)이 그 열을 갖고 있어 유지한다. 값은 늘 '전 기간'이다.
        "split": "all",
        "split_start": split_start.strftime("%Y-%m-%d"),
        "split_end": split_end.strftime("%Y-%m-%d"),
        "base_date": base_date.strftime("%Y-%m-%d"),
        "universe": universe,
        "skipped": skipped,
        "per_symbol": per_symbol,
        "metrics": _aggregate(all_trades),
    }
