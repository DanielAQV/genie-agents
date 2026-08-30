"""도구 루프 — 모델을 부르고, 도구를 돌리고, 언제 끝낼지 안다.

━━ 왜 한 벌만 두나 ━━

이 루프에 든 판단들은 **모델과도 인격과도 무관하다.**

  · 도구를 부르는 턴에 같이 온 글은 답이 아니다
  · 도구가 막혀서 되돌아온 것은 판단이 아니다
  · 도구가 다 돌았는데 답이 비면 한 번 더 묻는다
  · 판단이 도구 안에서 끝났으면 거기서 끊는다

에이전트마다 루프를 다시 쓰면 이 판단들을 매번 다시 검증해야 하고, 한 번
빠뜨리면 **그 에이전트만 조용히 다르게 행동한다.** 실제로 그렇게 갈린 것을
재보고 여기로 모았다.

갈리는 자리는 `policy.Policy` 에 이름으로 올라가 있다. 분기가 아니라 값이다.

━━ 무엇을 요구하나 ━━

    client   `messages.create(...)` (adapters/base.py 의 Client)
    session  `tools(scope)` 와 `call(name, **args)` 둘

`session` 이 게이트·과금·자리 제한을 들고 있다. 루프는 그 안을 안 본다 —
도구 목록이 매 요청 달라질 수 있다는 것만 안다.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from .policy import DEFAULT, Policy


class Session(Protocol):
    """루프가 에이전트에게 요구하는 것 — 둘뿐이다."""

    def tools(self, scope: str) -> list[dict]:
        """지금 이 자리에서 모델에게 보일 도구. **매 요청 물어본다** —
        잔고가 마르면 목록에서 빠지는 도구가 있다."""
        ...

    def call(self, name: str, **args: Any) -> dict:
        """도구를 돌린다. 막혔으면 `{"blocked": ...}` 를 돌려준다 —
        예외로 던지면 판단 루프가 거기서 끊긴다."""
        ...


@dataclass
class Turn:
    """한 번 돈 결과. 값과 무엇을 했는지가 여기 남는다.

    모델마다 값 매기는 방식이 다르므로(캐시 TTL 단가 · 프롬프트 크기 문턱)
    **여기서 돈으로 환산하지 않는다.** 토큰만 세고, 값은 어댑터나 쓰는 쪽이
    자기 표로 계산한다.
    """

    model: str = ""
    text: str = ""
    stop_reason: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    decided: bool = False
    input_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    requests: int = 0
    seconds: float = 0.0
    dropped: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)
    """모델이나 쓰는 쪽만 아는 값. 캐시 TTL 단가, 서버 도구 횟수 같은 것들이
    여기 들어간다 — 골격이 그 이름을 알면 그 모델 것이 되어 버린다.
    채우는 것은 `Policy.meter` 다."""

    @property
    def cache_hit_ratio(self) -> float:
        total = self.input_tokens + self.cached_tokens + self.cache_write_tokens
        return self.cached_tokens / total if total else 0.0


def move_cache_edge(messages: list[dict], previous: dict | None, ttl: str = "5m") -> dict | None:
    """캐시 경계를 마지막 사용자 메시지로 **옮긴다.** 앞의 것은 뗀다.

    **옮기는 것이지 더하는 것이 아니다.** 경계에는 개수 상한이 있어서 매번
    새로 달면 금방 넘는다.

    붙일 자리가 없으면 조용히 넘어간다 — 마지막 메시지가 어댑터 응답 객체일
    때가 있다. 캐시가 안 걸린다고 판단이 멈출 이유는 없다.
    """
    if previous is not None:
        previous.pop("cache_control", None)
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
            msg["content"] = content
        if isinstance(content, list) and content and isinstance(content[-1], dict):
            content[-1]["cache_control"] = {"type": "ephemeral", "ttl": ttl}
            return content[-1]
        return None
    return None


def _count(turn: Turn, response: Any, meter=None) -> None:
    """표준 넷을 센다. 그 밖은 `meter` 가 `turn.extra` 에 담는다."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    turn.input_tokens += getattr(usage, "input_tokens", 0) or 0
    turn.cached_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
    turn.cache_write_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0
    turn.output_tokens += getattr(usage, "output_tokens", 0) or 0
    if meter is not None:
        meter(turn, response)


def _clean(said: str, policy: Policy, turn: Turn) -> str:
    """다듬는 손들을 순서대로 지나간다. 걷어낸 것은 남긴다 —
    얼마나 자주 그러는지 알아야 다음을 정한다."""
    for hand in policy.sanitizers:
        said, dropped = hand(said)
        if dropped:
            turn.dropped.extend(dropped)
            print(f"  (걷어냈다: {' '.join(dropped)})", file=sys.stderr)
    return said


def result_blocks(out: Any, tool_use_id: str) -> list[dict]:
    """도구가 낸 것을 모델이 읽는 블록들로. **기본은 글 하나다.**

    ★ 그림처럼 글이 아닌 것을 어디에 두는지는 **어댑터 사정**이라 골격이 안
      정한다. 어떤 모델은 도구 결과 블록 안에 넣을 수 있고, 어떤 모델은 도구
      결과가 글만 받아서 **옆에 나란히** 놓아야 한다. 그때는
      `Policy.result_blocks` 로 갈아끼운다.
    """
    return [
        {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": json.dumps(out, ensure_ascii=False, default=str),
        }
    ]


def run(
    client: Any,
    session: Session,
    messages: list[dict],
    *,
    model: str,
    system: Any = None,
    scope: str = "",
    policy: Policy = DEFAULT,
    turn: Turn | None = None,
) -> Turn:
    """한 번 돈다. `messages` 는 **제자리에서 자란다** — 부른 쪽이 이어서 쓸 수 있게.

    `turn` 을 주면 그걸 채운다. 값 계산처럼 **모델마다 다른 것**을 얹은 하위
    클래스를 쓰는 자리다 — 골격이 그걸 알 필요는 없고, 알면 그 모델 것이 된다.
    """
    turn = turn if turn is not None else Turn()
    turn.model = model
    started = time.monotonic()
    edge: dict | None = None
    pauses = 0
    retried = False
    force = policy.force_first

    for _ in range(policy.max_turns):
        if policy.move_cache_edge:
            edge = move_cache_edge(messages, edge, policy.cache_ttl)

        call = dict(policy.extra)
        if force:
            call["tool_choice"] = {"type": "tool", "name": force}
            force = ""

        response = client.messages.create(
            model=model,
            max_tokens=policy.max_tokens,
            system=system,
            # **매 요청 물어본다.** 잔고가 마르면 빠지는 도구가 있다.
            tools=session.tools(scope),
            messages=messages,
            **call,
        )
        turn.requests += 1
        turn.stop_reason = getattr(response, "stop_reason", "") or ""
        _count(turn, response, policy.meter)

        chunks = [
            b.text for b in response.content
            if getattr(b, "type", "") == "text" and (b.text or "").strip()
        ]
        tool_turn = turn.stop_reason == "tool_use"

        if chunks and (not tool_turn or policy.keep_text_with_tool_call):
            said = _clean("\n\n".join(chunks), policy, turn)
            turn.text = said
            # 다듬고 났더니 남는 게 없으면 한 번만 다시 묻는다.
            if not said and policy.retry_when_empty and not retried:
                retried = True
                print("  (말이 통째로 걷어낼 것뿐이라 한 번 다시 묻는다)", file=sys.stderr)
                continue

        if turn.stop_reason == "pause_turn" and pauses < policy.max_pauses:
            # 서버 도구가 자기 한도에 걸린 것. 그대로 다시 보내면 이어서 돈다.
            pauses += 1
            messages.append({"role": "assistant", "content": response.content})
            continue

        if not tool_turn:
            # 도구는 다 돌았는데 답이 한 글자도 안 온 자리.
            if policy.retry_when_empty and not turn.text and not retried:
                retried = True
                continue
            break

        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in (b for b in response.content if getattr(b, "type", "") == "tool_use"):
            args = block.input if isinstance(block.input, dict) else json.loads(block.input)
            turn.tool_calls.append({"name": block.name, "input": args})
            try:
                out = session.call(block.name, **args)
                # ★ **부른 것과 선 것은 다르다.** 막혀서 아무것도 안 남기고
                #   되돌아온 것을 판단으로 치면 그 자리는 판단 없이 끝난다.
                if isinstance(out, dict) and out.get("blocked"):
                    turn.blocked.append(block.name)
                elif block.name in policy.decision_tools:
                    turn.decided = True
                shape = policy.result_blocks or result_blocks
                results.extend(shape(out, block.id))
            except Exception as e:  # noqa: BLE001 — 결과를 보고 판단하게 둔다
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"{type(e).__name__}: {e}",
                        "is_error": True,
                    }
                )

        if turn.decided and (not policy.decision_scopes or scope in policy.decision_scopes):
            break

        messages.append({"role": "user", "content": results})

    turn.seconds = time.monotonic() - started
    return turn
