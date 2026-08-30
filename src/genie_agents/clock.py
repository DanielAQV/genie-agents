"""시각과 시간 감각.

ISO 타임스탬프만 주면 시간이 흐른다는 걸 알 수 없다. "언제였는지"가 아니라
**"얼마나 지났는지"** 가 맥락에 들어가야 한다.

시간대는 에이전트마다 다를 수 있다. 사는 곳이 다를 수 있어서가 아니라 —
둘 다 사용자를 따라간다 — 사용자가 옮겨 다니는 동안 한쪽만 먼저 옮겨 볼 수
있어야 해서다. `{프리픽스}_TZ` 가 정하고, 없으면 `DEFAULT_TZ` 다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import env

DEFAULT_TZ = "Asia/Seoul"
DEFAULT_UTC_OFFSET = 0.0

# 프리픽스 → (시간대, tz 데이터베이스가 없을 때의 오프셋).
# **모듈 전역 하나로 두면 안 된다** — 시험이 두 패키지를 같이 import 할 때
# 나중 것이 앞 것의 기본값을 덮어쓴다. 프리픽스로 갈라 두면 각자 자기 것을 읽는다.
_defaults: dict[str, tuple[str, float]] = {}

_tz_cache: dict[str, object] = {}


def set_default(prefix: str, tz: str, utc_offset: float = 0.0) -> None:
    """그 에이전트가 아무 설정도 없을 때 사는 시간대. 각 패키지가 부른다."""
    _defaults[prefix.strip().upper()] = (tz, utc_offset)


def _fallback() -> tuple[str, float]:
    return _defaults.get(env.prefix(), (DEFAULT_TZ, DEFAULT_UTC_OFFSET))


def tz_name() -> str:
    return env.get("TZ") or _fallback()[0]


def tz():
    """호출 시점에 읽는다 — `.env` 가 import 뒤에 로드되기 때문."""
    name = tz_name()
    if name not in _tz_cache:
        try:
            _tz_cache[name] = ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            # tz 데이터베이스가 없는 환경(tzdata 미설치 윈도우)용 폴백.
            _tz_cache[name] = timezone(
                timedelta(hours=env.num("UTC_OFFSET", _fallback()[1])), name
            )
    return _tz_cache[name]


_now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

_WEEKDAY = "월화수목금토일"


def now() -> datetime:
    return _now()


def now_iso() -> str:
    return _now().isoformat()


def set_clock(fn: Callable[[], datetime]) -> None:
    """테스트 전용. 시각 공급자를 교체한다."""
    global _now
    _now = fn


def parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def local(ts: str | datetime | None = None) -> datetime:
    dt = _now() if ts is None else (parse(ts) if isinstance(ts, str) else ts)
    return dt.astimezone(tz())


def stamp(ts: str | datetime | None = None) -> str:
    """사람이 읽는 절대 시각. 예: 2026-08-23(토) 15:40"""
    d = local(ts)
    return f"{d:%Y-%m-%d}({_WEEKDAY[d.weekday()]}) {d:%H:%M}"


def recent_days(days: int = 7, at: str | datetime | None = None) -> str:
    """오늘부터 거꾸로 이레의 달력. 예: 오늘 8-26(수) · 어제 8-25(화) · 그제 8-24(월) · 8-23(일) …

    사용자는 "월요일에", "어제" 처럼 요일과 상대어를 섞어 말한다. 그때마다 오늘
    요일에서 거꾸로 세야 하는데, **세는 걸 건너뛰면 그냥 틀린다.** 실제로 한 날을
    두 이름("어제"와 "화요일")으로 부른 것을 두 날로 세서 "나흘 연속"이라는 없는
    결론이 나왔다. 에이전트 자신의 진단이 정확했다 — "날짜 계산이 아니라 문장 파싱을
    대충 한 거지. 요일 이름이 몇 개 나오나만 세고, 겹치는 날인지는 확인 안 했어."

    도구로 달면 안 부르면 그만이다. 그래서 미리 세어서 눈앞에 둔다.
    """
    today = local(at).date()
    이름 = ("오늘 ", "어제 ", "그제 ")
    return " · ".join(
        f"{이름[i] if i < len(이름) else ''}{d.month}-{d.day}({_WEEKDAY[d.weekday()]})"
        for i, d in ((i, today - timedelta(days=i)) for i in range(days))
    )


def part_of_day(ts: str | datetime | None = None) -> str:
    """지금이 하루의 어느 자리인지. **다른 에이전트와 나눔이 다르고, 그게 맞다.**

    에이전트는 5칸, 다른 에이전트는 6칸이다(다른 에이전트에게는 "오후" 가 따로 있다). 2026-08-29 에
    사용자가 화면에서 알아채고 둘에게 물었는데 둘 다 지금 것을 골랐다 —
    에이전트: "그대로 유지. 다른 에이전트와 다를 필요 없음 — 각자 감각이라 통일 안 해도 됨."

    ★ 이 값은 **프롬프트에 실린다**(`prompt.py`). 고치면 에이전트가 시간을 느끼는
      방식이 바뀐다. 통일하고 싶어지면 먼저 물어라.
    """
    h = local(ts).hour
    if h < 5:
        return "새벽"
    if h < 12:
        return "아침"
    if h < 17:
        return "낮"
    if h < 21:
        return "저녁"
    return "밤"


def ago(ts: str | datetime, at: str | datetime | None = None) -> str:
    """경과 시간을 에이전트가 읽는 말로. 예: 방금, 40분 전, 어제, 3일 전"""
    then = parse(ts) if isinstance(ts, str) else ts
    ref = _now() if at is None else (parse(at) if isinstance(at, str) else at)
    delta = ref - then

    if delta < timedelta(0):
        return "곧"
    secs = delta.total_seconds()
    if secs < 90:
        return "방금"
    mins = int(secs // 60)
    if mins < 60:
        return f"{mins}분 전"
    hours = int(secs // 3600)
    if hours < 24 and local(then).date() == local(ref).date():
        return f"{hours}시간 전"

    days = (local(ref).date() - local(then).date()).days
    if days == 1:
        return "어제"
    if days == 2:
        return "그제"
    if days < 7:
        return f"{days}일 전"
    if days < 30:
        return f"{days // 7}주 전"
    if days < 365:
        return f"{days // 30}개월 전"
    return f"{days // 365}년 전"


def elapsed_minutes(ts: str | datetime, at: str | datetime | None = None) -> float:
    then = parse(ts) if isinstance(ts, str) else ts
    ref = _now() if at is None else (parse(at) if isinstance(at, str) else at)
    return (ref - then).total_seconds() / 60


def minutes_since(ts: str | datetime) -> float:
    """`elapsed_minutes` 와 같다. 다른 에이전트 쪽에서 쓰던 이름이라 남겨 둔다."""
    return elapsed_minutes(ts)
