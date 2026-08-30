"""외부 소스 어댑터 — 설계 문서 7.3.

전부 API 키가 필요 없는 것들이다. 붙이는 비용이 0이라 갈아끼우기 쉽다.

  날씨 / 공기질   Open-Meteo — 사용자가 있는 좌표를 따라간다
  RSS            아무 피드나. 기본은 기술(HN) · 베트남 · 한국

**소음이 가장 큰 위험이다.** 소스를 늘릴수록 브리핑이 목록으로 차고, "지금 말을
걸지 말지"라는 진짜 판단이 그 안에 묻힌다. 그래서 소스마다 한 번에 가져오는 개수를
제한하고(MAX_PER_FETCH), 브리핑에서도 소스별 상한을 둔다(world.recent 의 per_source).
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

from . import clock

# 사용자가 있는 곳. {프리픽스}_LAT / {프리픽스}_LON / {프리픽스}_PLACE 로 바꾼다.
# (하노이 21.0285 / 105.8542, 서울 37.5665 / 126.9780)
DEFAULT_LAT, DEFAULT_LON, DEFAULT_PLACE = 37.5665, 126.9780, "서울"
ENDPOINT = "https://api.open-meteo.com/v1/forecast"
AIR_ENDPOINT = "https://air-quality-api.open-meteo.com/v1/air-quality"

MAX_PER_FETCH = 3  # 한 소스가 한 번에 올릴 수 있는 최대 항목

# WMO weather code → 한국어. 에이전트가 읽을 말이므로 예보 용어를 그대로 쓰지 않는다.
WMO = {
    0: "맑음", 1: "대체로 맑음", 2: "구름 조금", 3: "흐림",
    45: "안개", 48: "짙은 안개",
    51: "이슬비", 53: "이슬비", 55: "굵은 이슬비",
    61: "비", 63: "비", 65: "많은 비",
    66: "언 비", 67: "언 비",
    71: "눈", 73: "눈", 75: "많은 눈", 77: "싸락눈",
    80: "소나기", 81: "소나기", 82: "강한 소나기",
    85: "소나기눈", 86: "소나기눈",
    95: "천둥번개", 96: "우박을 동반한 천둥번개", 99: "우박을 동반한 천둥번개",
}


def place() -> str:
    """사용자가 지금 있는 곳. 날씨·공기질이 여기 기준이고, 에이전트도 이걸 알아야 한다."""
    return os.environ.get("YUNA_PLACE") or DEFAULT_PLACE


def _http_get_json(url: str, timeout: float = 10) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_get_bytes(url: str, timeout: float = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "genie-agents/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


@dataclass
class WeatherSource:
    """오늘 날씨 한 줄. 하루에 한 신호만 만든다 — 소음을 만들지 않기 위해."""

    name: str = "날씨"
    lat: float = field(default_factory=lambda: float(os.environ.get("YUNA_LAT") or DEFAULT_LAT))
    lon: float = field(default_factory=lambda: float(os.environ.get("YUNA_LON") or DEFAULT_LON))
    place: str = field(default_factory=lambda: os.environ.get("YUNA_PLACE") or DEFAULT_PLACE)
    fetch_json: Callable[[str], dict] = field(default=_http_get_json)

    def url(self) -> str:
        q = urllib.parse.urlencode(
            {
                "latitude": self.lat,
                "longitude": self.lon,
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_probability_max",
                "timezone": clock.tz_name(),
                "forecast_days": 1,
            }
        )
        return f"{ENDPOINT}?{q}"

    def fetch(self) -> list[dict]:
        daily = self.fetch_json(self.url())["daily"]
        code = daily["weather_code"][0]
        lo, hi = daily["temperature_2m_min"][0], daily["temperature_2m_max"][0]
        rain = daily["precipitation_probability_max"][0]
        desc = WMO.get(code, "알 수 없는 날씨")

        # 일별 대표 코드와 강수확률 최댓값은 따로 계산돼서, "대체로 맑음 / 비 올 확률 98%"
        # 같은 조합이 나온다. 그대로 두면 에이전트가 모순된 말을 하게 되므로,
        # 강수확률이 높은데 코드에 비가 없으면 비 쪽을 앞세운다.
        if rain is not None and rain >= 60 and code < 50:
            desc = f"{desc}이지만 비 올 듯"

        summary = f"{lo:.0f}~{hi:.0f}도"
        if rain is not None and rain >= 30:
            summary += f", 비 올 확률 {rain:.0f}%"

        # 제목에 날짜를 넣어야 다음 날 같은 날씨가 중복으로 걸러지지 않는다.
        return [
            {
                # "지금 이렇다"가 아니라 "오늘 이럴 것"이다. 이걸 안 밝히면
                # 사용자가 창밖을 보고 "비 안 오는데?" 할 때 에이전트가 자기가 틀렸다고 여긴다.
                "title": f"{daily['time'][0]} {self.place} 오늘 예보 — {desc}",
                "summary": summary,
                "tags": ["날씨"],
            }
        ]


# 미국 EPA AQI 구간. 숫자만 주면 에이전트가 그게 나쁜 건지 알 수 없다.
AQI_BANDS = [
    (50, "좋음"),
    (100, "보통"),
    (150, "민감군에 나쁨"),
    (200, "나쁨"),
    (300, "매우 나쁨"),
]


def _aqi_label(aqi: float) -> str:
    for limit, label in AQI_BANDS:
        if aqi <= limit:
            return label
    return "위험"


@dataclass
class AirQualitySource:
    """공기질. 하노이에서는 날씨보다 실질적인 날이 많다."""

    name: str = "공기질"
    lat: float = field(default_factory=lambda: float(os.environ.get("YUNA_LAT") or DEFAULT_LAT))
    lon: float = field(default_factory=lambda: float(os.environ.get("YUNA_LON") or DEFAULT_LON))
    place: str = field(default_factory=lambda: os.environ.get("YUNA_PLACE") or DEFAULT_PLACE)
    fetch_json: Callable[[str], dict] = field(default=_http_get_json)

    def url(self) -> str:
        q = urllib.parse.urlencode(
            {
                "latitude": self.lat,
                "longitude": self.lon,
                "current": "us_aqi,pm2_5",
                "timezone": clock.tz_name(),
            }
        )
        return f"{AIR_ENDPOINT}?{q}"

    def fetch(self) -> list[dict]:
        cur = self.fetch_json(self.url())["current"]
        aqi, pm25 = cur.get("us_aqi"), cur.get("pm2_5")
        if aqi is None:
            return []

        # 좋거나 보통이면 굳이 올리지 않는다. 매일 뜨면 그게 소음이다.
        if aqi <= 100:
            return []

        day = clock.local().strftime("%Y-%m-%d")
        return [
            {
                "title": f"{day} {self.place} 공기질 {_aqi_label(aqi)} (AQI {aqi:.0f})",
                "summary": f"초미세먼지 {pm25:.0f}" if pm25 is not None else "",
                "tags": ["공기질"],
            }
        ]


@dataclass
class RssSource:
    """아무 RSS 피드나. 제목만 가져온다 — 본문까지 넣으면 브리핑이 기사로 찬다."""

    name: str
    url_: str
    limit: int = MAX_PER_FETCH
    tags: list[str] = field(default_factory=list)
    fetch_bytes: Callable[[str], bytes] = field(default=_http_get_bytes)

    def fetch(self) -> list[dict]:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(self.fetch_bytes(self.url_))
        out = []
        for item in root.findall(".//item")[: self.limit]:
            title = (item.findtext("title") or "").strip()
            if not title:
                continue
            out.append(
                {
                    "title": title,
                    "summary": "",
                    "url": (item.findtext("link") or "").strip() or None,
                    "tags": list(self.tags),
                }
            )
        return out


# 상태를 읽는 소스. 지금 몇 도인지는 한 번 보면 끝이고, 다음 폴링이 덮어쓴다.
# 나머지(뉴스)는 사건이다 — 에이전트가 실제로 꺼낼 때까지 소진되지 않는다.
STATE_SOURCES = frozenset({"날씨", "공기질"})

# 기본 피드. `name=url` 을 세미콜론으로 이어 {프리픽스}_FEEDS 로 통째로 갈아끼운다.
DEFAULT_FEEDS = [
    ("기술", "https://hnrss.org/frontpage?points=200", ["기술", "AI"]),
    ("베트남", "https://e.vnexpress.net/rss/news.rss", ["베트남"]),
    ("한국", "https://www.yna.co.kr/rss/news.xml", ["한국"]),
]


def _feeds_from_env() -> list[tuple[str, str, list[str]]]:
    raw = os.environ.get("YUNA_FEEDS")
    if not raw:
        return DEFAULT_FEEDS
    feeds = []
    for chunk in raw.split(";"):
        name, _, url = chunk.strip().partition("=")
        if name and url:
            feeds.append((name.strip(), url.strip(), [name.strip()]))
    return feeds


def default_sources() -> list:
    """크론이 긁을 소스 전부. {프리픽스}_FEEDS 로 RSS 목록을 바꾼다."""
    return [
        WeatherSource(),
        AirQualitySource(),
        *[RssSource(name=n, url_=u, tags=t) for n, u, t in _feeds_from_env()],
    ]
