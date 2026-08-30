"""Gemini 어댑터 — `Client` 모양으로 옮긴다.

루프는 한 가지 메시지 모양만 본다(`base.py`). 여기서 Gemini 를 그 모양으로
옮긴다. 루프를 Gemini 모양으로 다시 쓰지 않는 이유는 둘이다.

  1. 루프에 든 판단들은 모델과 무관하다. 옮겨 적으면 그 판단들을 다시
     검증해야 한다.
  2. 모델을 또 바꾸게 되면 갈아끼울 자리가 이 파일 하나로 남는다.

━━ 옮기면서 버리는 것 ━━

★ **`cache_control` 은 무시한다.** Gemini 의 캐싱은 접두사 캐시가 아니라 그대로
  옮길 수가 없다. **없애지 않고 무시만 한다** — 프롬프트가 캐시 경계를 의식해서
  짜여 있고(안 변하는 것을 앞에, 변하는 것을 뒤에) 그 구조는 모델이 바뀌어도
  나쁠 것이 없다. 나중에 명시적 캐싱을 붙일 자리도 거기 그대로 남는다.

  **그래서 `Policy.move_cache_edge` 를 켜 둬도 이 어댑터에서는 아무 일도 안
  일어난다.** 도는 줄 알고 유지보수하지 않게 여기 적어 둔다.

★ 서버 도구(`pause_turn`)가 없다. `Policy.max_pauses` 는 0 으로 둬라.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass

from .. import env
from .base import Response, TextBlock, ToolUseBlock, Usage

DEFAULT_MODEL = "gemini-2.5-pro"
DEFAULT_FAST_MODEL = "gemini-2.5-flash"  # 정할 게 하나뿐인 값싼 자리

_SCHEMA_KEYS = frozenset(
    {"type", "description", "properties", "required", "items", "enum", "nullable"}
)


class GeminiUnavailable(RuntimeError):
    """google-genai 가 없거나 키가 없다."""


def sanitize_schema(schema: dict) -> dict:
    """JSON Schema 에서 Gemini 가 아는 것만 남긴다.

    모르는 키가 있으면 요청 자체가 400 으로 떨어진다. 도구 하나가 통째로 못
    쓰이게 되느니 설명 몇 줄을 잃는 편이 낫다.
    """
    out: dict[str, Any] = {}
    for k, v in schema.items():
        if k not in _SCHEMA_KEYS:
            continue
        if k == "properties" and isinstance(v, dict):
            out[k] = {pk: sanitize_schema(pv) for pk, pv in v.items()}
        elif k == "items" and isinstance(v, dict):
            out[k] = sanitize_schema(v)
        else:
            out[k] = v
    return out


def to_declarations(tools: list[dict]) -> list[dict]:
    """Anthropic 도구 명세 → Gemini function declaration."""
    return [
        {
            "name": t["name"],
            "description": t.get("description", ""),
            "parameters": sanitize_schema(t.get("input_schema") or {}),
        }
        for t in tools
    ]


def system_text(system) -> str:
    """system 블록들을 한 덩어리로. cache_control 은 여기서 떨어진다."""
    if isinstance(system, str):
        return system
    return "\n\n".join(b.get("text", "") for b in system if b.get("text"))


def _part_text(part) -> str:
    if isinstance(part, str):
        return part
    if isinstance(part, dict):
        return part.get("text", "")
    return getattr(part, "text", "") or ""


def to_contents(messages: list[dict]) -> list[dict]:
    """루프가 만든 메시지들 → Gemini contents.

    네 모양이 들어온다.
      user   / 문자열 또는 [{"type":"text"}...]
      user   / [{"type":"media","mime":...,"data":bytes}] → inline_data
      user   / [{"type":"tool_result", ...}]        → function_response
      model  / [TextBlock | ToolUseBlock ...]       → function_call
    """
    contents: list[dict] = []
    # tool_use_id → 함수 이름. Gemini 의 function_response 는 id 가 아니라
    # 이름으로 짝을 맞춘다.
    names: dict[str, str] = {}

    for msg in messages:
        role = "user" if msg["role"] == "user" else "model"
        content = msg["content"]
        parts: list[dict] = []

        if isinstance(content, str):
            parts.append({"text": content})
        else:
            for block in content:
                kind = block.get("type") if isinstance(block, dict) else getattr(block, "type", "")

                if kind == "tool_result":
                    tid = block["tool_use_id"]
                    parts.append(
                        {
                            "function_response": {
                                "name": names.get(tid, tid),
                                "response": {"content": block.get("content", "")},
                            }
                        }
                    )
                elif kind == "tool_use":
                    name = block.name if not isinstance(block, dict) else block["name"]
                    args = block.input if not isinstance(block, dict) else block["input"]
                    bid = block.id if not isinstance(block, dict) else block["id"]
                    names[bid] = name
                    parts.append({"function_call": {"name": name, "args": args}})
                elif kind == "media":
                    # 사진·소리. **base64 가 아니라 바이트 그대로 넘긴다** —
                    # google-genai 가 bytes 를 받으면 알아서 싣는다. 여기서 미리
                    # 인코딩하면 그쪽에서 한 번 더 해서 두 배로 커진다.
                    parts.append(
                        {"inline_data": {"mime_type": block["mime"], "data": block["data"]}}
                    )
                else:
                    text = _part_text(block)
                    if text:
                        parts.append({"text": text})

        if parts:
            contents.append({"role": role, "parts": parts})
    return contents


def from_response(raw) -> Response:
    """Gemini 응답 → 루프가 아는 모양."""
    blocks: list = []
    stop = "end_turn"

    candidates = getattr(raw, "candidates", None) or []
    for cand in candidates[:1]:
        content = getattr(cand, "content", None)
        for i, part in enumerate(getattr(content, "parts", None) or []):
            call = getattr(part, "function_call", None)
            if call is not None and getattr(call, "name", None):
                blocks.append(
                    ToolUseBlock(
                        # Gemini 는 호출 id 를 안 준다. 짝만 맞으면 되므로 여기서 만든다.
                        id=f"call_{len(blocks)}_{call.name}",
                        name=call.name,
                        input=dict(getattr(call, "args", None) or {}),
                    )
                )
                stop = "tool_use"
                continue
            text = getattr(part, "text", None)
            if text:
                blocks.append(TextBlock(text=text))

    meta = getattr(raw, "usage_metadata", None)
    usage = Usage(
        # ★ **캐시분을 뺀다.** Gemini 의 `prompt_token_count` 는 캐시로 읽은 것을
        #   **포함한** 프롬프트 전체다. Anthropic 의 `input_tokens` 는 그걸 뺀
        #   값이라, 그대로 옮기면 캐시분이 두 번 세어진다 — 값 계산에서 입력
        #   단가로 한 번, 캐시 단가로 또 한 번. 캐시가 잘 먹을수록 더 틀린다
        #   (캐시 90%면 4.3배 부풀려졌다). 여기가 모양을 맞추는 자리다.
        input_tokens=max(
            0,
            (getattr(meta, "prompt_token_count", 0) or 0)
            - (getattr(meta, "cached_content_token_count", 0) or 0),
        ),
        output_tokens=getattr(meta, "candidates_token_count", 0) or 0,
        cache_read_input_tokens=getattr(meta, "cached_content_token_count", 0) or 0,
    )
    return Response(content=blocks, stop_reason=stop, usage=usage)


# --- 클라이언트 ---


class GeminiClient:
    """`client.messages.create(...)` 를 흉내 내는 얇은 어댑터.

    `agent.py` 는 이 객체가 Anthropic 클라이언트인지 아닌지 모른다.
    """

    def __init__(self, api_key: str | None = None, client=None) -> None:
        self._client = client
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY")

    @property
    def messages(self):
        return self

    @property
    def client(self):
        if self._client is None:
            if not self._api_key:
                raise GeminiUnavailable("GEMINI_API_KEY 가 없다")
            try:
                from google import genai
            except ImportError as e:  # pragma: no cover - 설치 안 된 환경
                raise GeminiUnavailable(f"google-genai 를 가져올 수 없다: {e}") from e
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def create(
        self,
        model: str,
        messages: list[dict],
        system=None,
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
        **ignored,
    ) -> Response:
        """`ignored` 에 들어오는 것들(cache_control 이 붙은 블록, output_config 등)은
        조용히 버린다. 루프가 Anthropic 쪽 인자를 그대로 넘기기 때문이고,
        여기서 막으면 모델을 바꿀 때마다 루프를 고쳐야 한다."""
        config: dict[str, Any] = {}
        text = system_text(system)
        if text:
            config["system_instruction"] = text
        if max_tokens:
            config["max_output_tokens"] = max_tokens
        if tools:
            config["tools"] = [{"function_declarations": to_declarations(tools)}]

        raw = self.client.models.generate_content(
            model=model,
            contents=to_contents(messages),
            config=config,
        )
        return from_response(raw)


def default_model(fast: bool = False) -> str:
    """`{프리픽스}_MODEL` 이 있으면 그것. `fast` 는 값싼 자리용이다.

    프리픽스는 에이전트마다 다르다 — 한 호스트에 여럿이 살면서 서로의 설정을
    안 밟는다(`env.py`).
    """
    if fast:
        return env.get("FAST_MODEL") or env.get("MODEL") or DEFAULT_FAST_MODEL
    return env.get("MODEL") or DEFAULT_MODEL


def available() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY"))
