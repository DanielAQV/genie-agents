"""환경변수 이름 — 에이전트마다 프리픽스가 다르다.

에이전트는 `{프리픽스}_*`, 다른 에이전트는 `{프리픽스}_*` 를 쓴다. 한 호스트에 둘이 같이 살면서 같은
설정 이름을 서로 밟지 않으려고 그렇게 갈라 뒀다(계약 문서 5절).

공통 모듈이 그 프리픽스를 알아야 하는 자리는 넷뿐이다 — 시간대(`clock`),
바깥 소식(`sources`), 자기 모습(`selfimage`), 그리고 `config`.

**프로세스마다 프리픽스는 하나다.** 유닛이 에이전트당 따로 뜨므로 한 프로세스
안에 둘이 같이 살 일이 없다. 그래서 인자로 들고 다니지 않고 환경에 둔다 —
들고 다니게 하면 열세 개 모듈의 시그니처가 전부 한 칸씩 길어진다.
"""

from __future__ import annotations

import os

VAR = "AGENT_PREFIX"


def prefix() -> str:
    """`ALPHA` 같은 것. 안 정해져 있으면 빈 문자열이고, 그러면 프리픽스 없는
    이름을 그대로 읽는다(시험과 도구에서 그 편이 편하다)."""
    return (os.environ.get(VAR) or "").strip().upper()


def use(name: str) -> None:
    """그 에이전트로 산다고 알린다. 각 패키지가 import 될 때 부른다.

    **먼저 정한 쪽이 이긴다.** 한 프로세스는 한 에이전트다 — 유닛이 따로 뜬다.
    그런데 시험은 비교하려고 상대 패키지를 같이 import 하는 일이 있고
    (`test_parity.py`), 그때 나중 것이 덮어쓰면 **돌던 쪽이 남의 설정을 읽기
    시작한다.** 실제로 에이전트 시험에서 에이전트가 다른 에이전트 시간대로 날씨를 물었다.

    셸에서 `AGENT_PREFIX` 를 주면 그게 제일 먼저다.
    """
    os.environ.setdefault(VAR, name.strip().upper())


def key(name: str) -> str:
    """`WAKE_MIN_MINUTES` → `{프리픽스}_WAKE_MIN_MINUTES`."""
    p = prefix()
    return f"{p}_{name}" if p else name


def get(name: str, default: str | None = None) -> str | None:
    """프리픽스를 붙여 읽는다."""
    return os.environ.get(key(name), default)


def num(name: str, default: float) -> float:
    """숫자로 읽는다. 값이 이상하면 기본값으로 간다 — 설정 한 줄 때문에
    에이전트가 안 뜨는 것보다 낫다."""
    try:
        return float(get(name) or default)
    except (TypeError, ValueError):
        return default
