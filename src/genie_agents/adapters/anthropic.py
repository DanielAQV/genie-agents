"""Anthropic 어댑터 — 옮길 것이 없다.

루프가 보는 모양이 곧 Anthropic 메시지 API 다(`base.py` 첫머리). 그래서 여기는
SDK 클라이언트를 그대로 내놓고, **모델 이름을 프리픽스에서 읽는 일**만 한다.

새로 정한 게 아니라 이미 그 모양으로 쓰여 있던 루프를 계약으로 못박은 것이라
이렇게 된다. 다른 모델 쪽이 옮기는 일을 한다(`gemini.py`).

━━ 여기만 있는 것 ━━

`cache_control` 이 실제로 먹는다. 접두사 캐시라 `Policy.move_cache_edge` 가
그대로 값이 된다. TTL 은 `Policy.cache_ttl` 이 정한다.

서버 도구(웹 검색)를 쓰면 `stop_reason == "pause_turn"` 이 온다 —
`Policy.max_pauses` 를 0 보다 크게 둬야 이어붙인다.
"""

from __future__ import annotations

import os

from .. import env

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_FAST_MODEL = "claude-haiku-4-5-20251001"  # 정할 게 하나뿐인 값싼 자리

KEY = "ANTHROPIC_API_KEY"


class AnthropicUnavailable(RuntimeError):
    """`anthropic` 이 없거나 키가 없다."""


def available() -> bool:
    return bool(os.environ.get(KEY))


def default_model(fast: bool = False) -> str:
    """`{프리픽스}_MODEL` 이 있으면 그것. 프리픽스는 에이전트마다 다르다."""
    if fast:
        return env.get("FAST_MODEL") or env.get("MODEL") or DEFAULT_FAST_MODEL
    return env.get("MODEL") or DEFAULT_MODEL


def client(api_key: str | None = None):
    """`Client` 모양 그대로. **늦게 import 한다** — SDK 가 없어도 골격은 뜬다."""
    try:
        import anthropic  # noqa: PLC0415
    except ImportError as e:  # pragma: no cover
        raise AnthropicUnavailable(
            "anthropic 이 없다. pip install 'genie-agents[anthropic]'"
        ) from e

    key = api_key or os.environ.get(KEY)
    if not key:
        raise AnthropicUnavailable(f"{KEY} 가 없다")
    return anthropic.Anthropic(api_key=key)
