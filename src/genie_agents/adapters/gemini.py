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

★ **`tool_choice` 는 버리지 않는다.** 하는 말이 Gemini 에도 있어서(`tool_config`)
  번역한다. 예전엔 이것도 `**ignored` 로 흘려서, 루프가 건 강제가 **조용히 안
  걸렸다** — 부르는 쪽은 걸린 줄 알고 있었다. 버릴 것과 옮길 것을 가르는 기준은
  "저쪽에 같은 말이 있나" 이지 "우리가 안 쓰나" 가 아니다.
"""

from __future__ import annotations

import json
import os
import re
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
    """google-genai 가 없거나 키가 없다.

    무엇이 없는지와 **무엇을 하면 되는지**를 같이 적는다 — 띄우다 막힌 사람이
    이 한 줄 말고 볼 것이 없다.
    """

    def __init__(self, why: str) -> None:
        super().__init__(
            f"Gemini 를 부를 수 없다: {why}\n"
            "`pip install google-genai` 후 GEMINI_API_KEY 를 채워야 한다."
        )


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

    def create(  # noqa: D417
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
        # ★ **이건 버리지 않고 번역한다.** 루프의 `Policy.force_first` 가 여기로
        #   온다("이 도구를 반드시 불러라"). 버리면 강제가 조용히 안 걸리고,
        #   부르는 쪽은 걸린 줄 안다 — 예나의 "사진이 오면 image_note 를 반드시"
        #   가 그렇게 한 번도 안 걸리고 있었다(2026-08-31에 알았다).
        picked = tool_config(ignored.get("tool_choice"))
        if picked:
            config["tool_config"] = picked

        raw = self.client.models.generate_content(
            model=model,
            contents=to_contents(messages),
            config=config,
        )
        return from_response(raw)


# Anthropic 의 `tool_choice` → Gemini 의 `tool_config`. 하는 말이 같아서 옮기면
# 된다. 옮기지 않으면 강제가 **조용히 안 걸린다** — 그게 제일 나쁜 모양이다.
_MODE = {"auto": "AUTO", "any": "ANY", "none": "NONE", "tool": "ANY"}


def tool_config(choice: Any) -> dict | None:
    """`{"type": "tool", "name": "voice_reply"}` → 그 도구만 반드시 부르게.

    모르는 모양이면 `None` — 여기서 넘겨짚어 엉뚱한 것을 강제하느니 안 거는
    편이 낫다. 안 걸린 것은 부르는 쪽 로그에 남지만, 엉뚱하게 걸린 것은 답이
    이상해질 뿐 어디에도 안 남는다.
    """
    if not isinstance(choice, dict):
        return None
    mode = _MODE.get(str(choice.get("type") or ""))
    if not mode:
        return None
    cfg: dict[str, Any] = {"mode": mode}
    name = choice.get("name")
    if name:
        cfg["allowed_function_names"] = [str(name)]
    return {"function_calling_config": cfg}


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


def client(api_key: str | None = None):
    """어댑터마다 같은 이름으로 낸다 — 러너가 이름 하나로 부른다."""
    return GeminiClient(api_key) if api_key else GeminiClient()


# ── Gemini 위에서 도는 에이전트가 공통으로 쓰는 것 ────────────────────
#
# 아래 넷은 **모델 사정**이지 그 에이전트가 누구인지가 아니다. 전에는 에이전트
# 파일 안에 있었고, 그래서 Gemini 위의 두 번째 에이전트는 값표부터 다시 적어야
# 했다. 갈리는 자리가 아니라 **어댑터가 감당할 자리**다.


@dataclass(frozen=True)
class Price:
    """$/MTok. `hi` 가 있는 모델은 프롬프트가 `threshold` 를 넘으면 그쪽을 쓴다.

    Gemini 는 캐시 **읽기**만 값이 다르다. 쓰기는 토큰당 값이 없어서(암묵 캐시)
    Anthropic 쪽의 5분/1시간 TTL 구분이 여기엔 없다.
    """

    inp: float
    out: float
    cache: float
    inp_hi: float = 0.0
    out_hi: float = 0.0
    cache_hi: float = 0.0
    threshold: int = 0


# 2026-08-29 ai.google.dev/gemini-api/docs/pricing 유료 티어 실측.
#
# **쓰는 모델이 여기 없으면 장부가 돈을 아예 안 찍는다** — cost 가 None 이 되고
# 토큰만 남는다. 모델을 갈아끼우면 여기도 같이 봐야 한다.
PRICES: dict[str, Price] = {
    "gemini-2.5-pro": Price(
        inp=1.25, out=10.00, cache=0.125,
        inp_hi=2.50, out_hi=15.00, cache_hi=0.25, threshold=200_000,
    ),
    "gemini-2.5-flash": Price(inp=0.30, out=2.50, cache=0.03),
}


def cost(turn) -> float | None:
    """이 턴에 나간 돈(USD). 값을 모르는 모델이면 None.

    ★ 문턱은 **한 프롬프트**로 본다. 토큰 수는 도구를 돌 때마다 더해지므로
      합계를 쓰면 150k 짜리를 두 번 돌았을 때 300k 로 세어져, 어느 요청도
      넘지 않았는데 턴 전체가 비싼 단가로 계산된다. 그래서 `meter` 가 제일 큰
      **한 요청**을 따로 들고 있는다.
    """
    price = PRICES.get(turn.model)
    if price is None:
        return None
    over = price.threshold and turn.extra.get("max_prompt_tokens", 0) > price.threshold
    inp = price.inp_hi if over else price.inp
    out = price.out_hi if over else price.out
    cached = price.cache_hi if over else price.cache
    return (
        # 캐시 쓰기는 Gemini 가 토큰당 값을 안 매긴다. 값이 들어오면 입력으로 친다.
        (turn.input_tokens + turn.cache_write_tokens) * inp
        + turn.cached_tokens * cached
        + turn.output_tokens * out
    ) / 1e6


def meter(turn, response) -> None:
    """값 문턱에 쓸 **한 요청의** 프롬프트 크기. 제일 큰 것을 들고 있는다.

    캐시로 읽은 것도 프롬프트 크기에는 들어가므로 캐시가 먹었다고 문턱 아래로
    내려가지 않지만, 도구를 여러 번 돈다고 넘어가지도 않는다.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    one = (
        (getattr(usage, "input_tokens", 0) or 0)
        + (getattr(usage, "cache_read_input_tokens", 0) or 0)
        + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
    )
    turn.extra["max_prompt_tokens"] = max(turn.extra.get("max_prompt_tokens", 0), one)


# 도구를 부르는 대신 글로 흉내 낸 것.
#
# Gemini 가 가끔 도구를 **안 부르고** 그 호출을 글로 적어서 뱉는다(`✨tool_code`
# 다음 줄에 `print(...)`). 사용자 화면에는 결과 대신 그 코드가 뜬다.
# ★ **스스로 굳는다** — 저 글이 그 에이전트 말로 기억에 남고 다음 턴 작업 기억에
#   실려서 모델이 "여기서는 이렇게 쓰는구나" 로 읽는다. 그래서 나가는 자리와 옛
#   말을 다시 싣는 자리 **두 군데**를 막아야 한다.
# 적힌 대로 **실행하지 않는다** — 모델이 쓴 코드가 그대로 도는 자리를 만드는 건
# 다른 종류의 위험이다. 흉내 낸 대목만 지우고 나머지 말은 그대로 내보낸다.
_TOOL_CODE = re.compile(r"[\s\u2728]*`{0,3}\s*tool_code\b.*?(?:```|\Z)", re.S)


def drop_tool_code(text: str) -> str:
    """도구 호출을 흉내 낸 대목을 걷어낸 글."""
    return _TOOL_CODE.sub("", text or "").strip()


def sanitize_tool_code(said: str) -> tuple[str, list[str]]:
    """`Policy.sanitizers` 자리. 안 한 일을 한 것처럼 보이게 하는 대목을 건다."""
    out = drop_tool_code(said)
    return out, ([] if out == said else ["도구를 글로 흉내 낸 대목"])


def blocks_beside(out: dict, tool_use_id: str) -> list[dict]:
    """도구 결과에 실려 온 그림을 결과 **옆에** 나란히 놓는다.

    안에 넣으면 Gemini 가 못 받는다 — `function_response.response` 는 글만
    받는다(`to_contents`). 어댑터 사정이라 골격이 안 정한다.

    같은 턴에 실어 주는 이유: 자기가 만든 그림을 못 보면 마음에 드는지 판단할
    수가 없다.
    """
    images = out.pop("_images", None) if isinstance(out, dict) else None
    return [
        {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": json.dumps(out, ensure_ascii=False),
        },
        *({"type": "media", "mime": mime, "data": raw} for raw, mime in (images or [])),
    ]
