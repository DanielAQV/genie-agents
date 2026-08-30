""".env 로더 — 의존성 없이 읽는다(python-dotenv 를 안 쓴다).

형식은 `KEY=VALUE` 한 줄씩, `#` 으로 시작하는 줄은 주석.

**이미 환경에 있는 값은 덮어쓰지 않는다.** 셸에서 준 값이 파일보다 우선이라
`{프리픽스}_MIN_GAP_MINUTES=1 python -m <에이전트> loop` 같은 일회성 덮어쓰기가 그대로 먹는다.

어느 키가 없으면 곤란한지는 에이전트마다 다르다(에이전트는 Anthropic, 다른 에이전트는
Gemini). 그건 각자의 `config.py` 가 안다.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_PATH = Path(".env")


def load_env(path: Path | str = DEFAULT_PATH) -> dict[str, str]:
    """`.env` 를 읽어 환경에 넣는다. 파일이 없으면 조용히 넘어간다."""
    p = Path(path)
    if not p.exists():
        return {}

    loaded: dict[str, str] = {}
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip("'").strip('"')
        if not key:
            continue
        loaded[key] = value
        os.environ.setdefault(key, value)  # 셸에서 준 값이 우선
    return loaded


def missing(*keys: str) -> list[str]:
    """없는 것만 골라 돌려준다. 프리픽스를 안 붙인다 — API 키는 공용 이름이다."""
    return [k for k in keys if not os.environ.get(k)]
