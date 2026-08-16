"""스모크 테스트 — 하네스가 살아있는지 최소 확인.

실제 로직 테스트는 각 레이어 구현과 함께 추가. 가드레일 테스트 방향은 docs/TESTING.md.
"""

import src
from src import layer1_data, layer2_llm_reading, layer3_strategy, layer4_execution


def test_version() -> None:
    assert src.__version__ == "0.0.1"


def test_layers_importable() -> None:
    # 4개 레이어 패키지가 import 되는지 (구조 무결성).
    for layer in (layer1_data, layer2_llm_reading, layer3_strategy, layer4_execution):
        assert layer.__doc__ is not None
