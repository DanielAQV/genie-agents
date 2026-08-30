"""어댑터 계약 — 모델이 무엇이든 루프는 한 모양만 본다.

이 모양은 Anthropic 메시지 API 를 따른다. 새로 정한 게 아니라 **이미 그 모양으로
쓰여 있던 루프를 그대로 계약으로 못박은 것**이다. 다른 모델은 어댑터가 옮긴다.

    resp = client.messages.create(model=..., max_tokens=..., system=...,
                                  tools=[...], messages=[...])
    resp.content      TextBlock | ToolUseBlock 들
    resp.stop_reason  "end_turn" | "tool_use" | ...
    resp.usage        토큰 수

━━ 왜 루프를 모델마다 다시 안 쓰나 ━━

루프에 든 판단들은 모델과 무관하다 — 도구를 부르는 턴에 같이 온 글은 답이
아니다, 막힌 도구는 판단이 아니다, 도구가 다 돌았는데 답이 비면 한 번 더 묻는다.
모델마다 루프를 다시 쓰면 그 판단들을 매번 다시 검증해야 하고, 한 번 빠뜨리면
그 모델을 쓰는 에이전트만 조용히 다르게 행동한다.

━━ 옮기다 못 옮기는 것 ━━

모델마다 없는 기능이 있다(캐시 제어, 서버 도구 등). **없애지 말고 무시한다.**
루프가 그 자리를 계속 갖고 있어야 나중에 붙일 자리가 남는다. 다만 무시하는
것은 어댑터 첫머리에 적는다 — 안 적으면 그 코드가 도는 줄 알고 유지보수한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict
    type: str = "tool_use"


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class Response:
    content: list = field(default_factory=list)
    stop_reason: str = "end_turn"
    usage: Usage = field(default_factory=Usage)


@runtime_checkable
class Messages(Protocol):
    def create(
        self,
        *,
        model: str,
        max_tokens: int,
        system: Any = None,
        tools: list[dict] | None = None,
        messages: list[dict],
        **extra: Any,
    ) -> Response: ...


@runtime_checkable
class Client(Protocol):
    """어댑터가 내놓아야 하는 것. `messages.create(...)` 하나다."""

    messages: Messages
