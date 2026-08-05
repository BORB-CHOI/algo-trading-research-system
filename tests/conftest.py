import pytest


@pytest.fixture(autouse=True)
def no_theme_crawl(monkeypatch):
    """테스트가 네이버 테마 수집(백그라운드 네트워크)을 깨우지 않게 막는다."""
    import api.main

    monkeypatch.setattr(api.main, "theme_map", lambda: ({}, True))
