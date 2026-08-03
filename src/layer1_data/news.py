"""뉴스 수집 — 증시 전체(RSS) + 종목별(네이버) (BORB-43).

증시 뉴스는 언론사 공개 RSS, 종목 뉴스는 네이버 종목뉴스 응답을 쓴다.
표시 전용이다 — 매매 판단·신호 계산에 쓰지 않는다(뉴스 신호는 Layer 2, Phase 2).
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET

import requests

HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}
TIMEOUT = 8
CACHE_TTL_SEC = 120

RSS_FEEDS = [
    ("한국경제", "https://www.hankyung.com/feed/finance"),
    ("연합뉴스", "https://www.yna.co.kr/rss/economy.xml"),
]

_cache: dict[str, tuple[float, list[dict]]] = {}


def _cached(key: str):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL_SEC:
        return hit[1]
    return None


def market_news(limit: int = 30) -> list[dict]:
    cached = _cached("market")
    if cached is not None:
        return cached[:limit]

    items: list[dict] = []
    for source, url in RSS_FEEDS:
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            root = ET.fromstring(r.content)
            for it in root.findall(".//item"):
                title = (it.findtext("title") or "").strip()
                if not title:
                    continue
                items.append({
                    "title": title,
                    "source": source,
                    "url": (it.findtext("link") or "").strip(),
                    "datetime": (it.findtext("pubDate") or "").strip(),
                })
        except Exception:  # noqa: BLE001 — 한 소스가 죽어도 나머지는 보여준다
            continue

    _cache["market"] = (time.time(), items)
    return items[:limit]


def stock_news(code: str, limit: int = 20) -> list[dict]:
    code = code.strip().zfill(6)
    cached = _cached(f"stock:{code}")
    if cached is not None:
        return cached[:limit]

    url = f"https://m.stock.naver.com/api/news/stock/{code}?pageSize={limit}&page=1"
    items: list[dict] = []
    try:
        data = requests.get(url, headers=HEADERS, timeout=TIMEOUT).json()
        for block in data if isinstance(data, list) else []:
            for it in block.get("items", []):
                dt = str(it.get("datetime", ""))
                items.append({
                    "title": str(it.get("title", "")).replace("&quot;", '"').strip(),
                    "source": it.get("officeName", ""),
                    "url": f"https://n.news.naver.com/mnews/article/{it.get('officeId')}/{it.get('articleId')}",
                    "datetime": f"{dt[:4]}-{dt[4:6]}-{dt[6:8]} {dt[8:10]}:{dt[10:12]}" if len(dt) >= 12 else dt,
                })
    except Exception:  # noqa: BLE001
        return []

    _cache[f"stock:{code}"] = (time.time(), items)
    return items[:limit]
