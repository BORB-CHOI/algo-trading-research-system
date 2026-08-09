"""파생 데이터 읽기 창구 — 사전 계산된 수정주가 일봉 (data/derived/adjusted).

`scripts/build_adjusted.py` 가 미리 만들어 둔 종목별 parquet 을 읽는 얇은 헬퍼다.
보정 계산의 정본은 layer1 `adjust.apply_split_adjustment`(ADR-0006) — 여기서는
어떤 보정도 다시 하지 않고 파일을 그대로 돌려준다.

파일 규격 (build_adjusted.py 와의 계약):
- 경로: data/derived/adjusted/{6자리 종목코드}.parquet
- 컬럼: Date, Open, High, Low, Close, Volume, Amount, Marcap, Stocks (Date 오름차순)
  OHLC·Volume 은 분할/병합 보정값, Amount·Marcap·Stocks 는 원본 그대로.
- meta.json: 생성 시각(generated_at)·소스 마지막 거래일(source_last_date) 등.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

# 저장 위치 — build_adjusted.py 와 공유하는 정본 경로 (프로젝트 루트 기준 상대경로,
# marcap_loader.MARCAP_DIR 과 같은 관례: 실행 cwd = 저장소 루트를 전제한다).
DERIVED_DIR = Path("data/derived")
ADJUSTED_DIR = DERIVED_DIR / "adjusted"
META_NAME = "meta.json"


def load_adjusted(code: str, adjusted_dir: Path = ADJUSTED_DIR) -> pd.DataFrame | None:
    """한 종목의 수정주가 일봉을 읽는다. 파일이 없으면 None.

    None 은 "빌드가 안 됐거나 그 종목이 marcap 에 없음"을 뜻한다 — 예외를 던지지
    않는 이유는 멀티종목 러너가 상폐/미빌드 종목을 건너뛰며 진행해야 하기 때문이다.
    구분이 필요하면 호출자가 meta.json(derived_last_date)으로 빌드 여부를 확인한다.
    """
    path = adjusted_dir / f"{str(code).strip().zfill(6)}.parquet"
    if not path.exists():
        return None
    return pd.read_parquet(path)


def derived_last_date(adjusted_dir: Path = ADJUSTED_DIR) -> pd.Timestamp | None:
    """빌드에 쓰인 소스(marcap)의 마지막 거래일. meta.json 이 없으면 None.

    러너/오케스트레이터가 "파생 데이터가 언제 기준인지"를 확인하는 용도다 —
    marcap 을 새로 받았는데 derived 가 옛날 것이면 재빌드가 필요하다.
    """
    meta_path = adjusted_dir / META_NAME
    if not meta_path.exists():
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    last = meta.get("source_last_date")
    return pd.Timestamp(last) if last else None


def drop_halted(df: pd.DataFrame) -> pd.DataFrame:
    """거래정지일 제거 — 체결이 없던 날은 봉이 아니다 (BORB-32).

    marcap 은 거래정지일을 OHLC 0원 · 거래량 0 으로 남긴다. 백테스트에서 이걸 안 걸면
    **저가가 0 이라 어떤 매수 지정가든 체결된 것으로 판정된다** — 실측 2026-08-10:
    전 기간 검사에서 -100.5% 같은(주식으로는 불가능한) 수익률이 나왔다. 033180 은
    1,482봉 중 668봉이 0원이었다.

    화면(`api.main`)과 전략(`surge._clean`)은 이미 같은 규칙을 쓰고 있었는데 백테스트
    경로만 빠져 있었다. 규칙은 한 곳에 둔다.
    """
    if df.empty:
        return df
    ok = (df["Open"] > 0) & (df["High"] > 0) & (df["Low"] > 0) & (df["Close"] > 0)
    if "Volume" in df.columns:
        ok &= df["Volume"] > 0
    return df[ok]
