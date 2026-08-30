"""모델을 갈아끼우는 자리.

도구 루프는 **한 가지 메시지 모양**으로 쓰여 있고, 어댑터가 각 모델을 그 모양으로
옮긴다. 루프에 든 판단들(도구 부르는 턴의 글은 답이 아니다 · 막힌 도구는 판단이
아니다)은 모델과 무관하므로, 모델마다 루프를 다시 쓰면 그 판단을 매번 다시
검증해야 한다.

계약은 `Client` 프로토콜 하나다 — `messages.create(...)` 를 받고
`.content` / `.stop_reason` / `.usage` 가 붙은 응답을 돌려준다.
"""

from .base import Client, Response, TextBlock, ToolUseBlock, Usage

__all__ = ["Client", "Response", "TextBlock", "ToolUseBlock", "Usage"]
