"""로컬 모델 — 이 기계 안에서 도는 것.

붙는 자리는 **OpenAI 호환 `/v1/chat/completions`** 하나다. llama.cpp 서버 ·
vLLM · Ollama 가 전부 그 모양을 낸다. 하나만 맞춰 두면 뒤를 갈아 끼워도
이쪽은 안 고친다 — 어댑터가 존재하는 이유와 같은 이유다.

    {프리픽스}_LOCAL_URL     기본 http://127.0.0.1:8080/v1/chat/completions
    {프리픽스}_LOCAL_MODEL   기본 로컬-모델 (llama.cpp 서버는 이름을 안 본다)

━━ 왜 SDK 를 안 쓰나 ━━

★ `pyproject.toml` 첫머리와 같은 규칙이다 — **의존성을 안 짊어진다.** 여기는
  urllib 한 겹이면 되고, 그러면 `pip install` 없이 도는 자리가 하나 는다.
  `sources.py` · `config.py` 가 이미 그렇게 쓰여 있다.

━━ 무시하는 것 (base.py 가 적으라고 한 자리) ━━

`adapters/base.py` — *"모델마다 없는 기능이 있다. 없애지 말고 무시한다.
다만 무시하는 것은 어댑터 첫머리에 적는다 — 안 적으면 그 코드가 도는 줄 알고
유지보수한다."*

  cache_control    프롬프트 캐시가 없다. 붙여 보내도 그냥 지나간다
  서버 도구        없다
  cache 토큰 수    `Usage` 의 캐시 칸은 항상 0 이다

━━ 도구 (2026-09-01 에 붙였다) ━━

★ 예전엔 **넘겨도 안 썼다.** 추출 자리에만 서 있었고 거기는 도구를 안 부르고
  JSON 하나를 받는 자리라서다. 유나의 일상 자리를 이리로 내리려니 필요해졌다 —
  그 자리마저 답을 `unseen_note` / `unseen_pass` 로 낸다.

OpenAI 쪽 `tools` / `tool_calls` 로 옮긴다. 옮기는 것이 넷이고, **되돌린
것**(`tool_result` → `role:"tool"`)이 제일 조용히 고장나는 자리다 — 그걸
글로 뭉개도 요청은 200 으로 돌아오고, 모델은 자기가 부른 도구가 무엇을 냈는지
모른 채 답한다.

━━ 이 어댑터가 서는 자리 ━━

★ 팀원의 글이 기계 밖으로 안 나가게 하려고 있는 것이다. 그래서 **주소가
  기본값으로 로컬호스트**이고, 다른 데를 가리키려면 그 값을 손으로 적어야 한다.
  적는 순간 그건 결정이 되고, 결정은 보이는 자리에 있어야 한다.
"""

from __future__ import annotations

import json
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request

from .. import env
from .base import Response, TextBlock, ToolUseBlock, Usage

DEFAULT_URL = "http://127.0.0.1:8080/v1/chat/completions"
DEFAULT_MODEL = "로컬-모델"
TIMEOUT = 900.0
"""4B 를 6GB 에서 돌리면 한 묶음에 1~2분이 걸린다(실측 2~4 tok/s).

★ 300초였을 때 실제로 한 번 끊겼고, 그 한 번이 **그 판 전체를 죽였다.**
  하루 열 번 안쪽으로 도는 물건이라 기다리는 값은 거의 0이고, 끊기는 값은
  그 판 전체다. 값이 대칭이 아니면 넉넉한 쪽으로 둔다."""


class LocalUnavailable(RuntimeError):
    """서버가 안 떠 있거나 답이 이상하다."""


def url() -> str:
    return env.get("LOCAL_URL") or DEFAULT_URL


def default_model(fast: bool = False) -> str:
    return env.get("LOCAL_MODEL") or DEFAULT_MODEL


def scopes(default: frozenset[str] = frozenset()) -> frozenset[str]:
    """이 기계 안의 모델로 갈 자리들. **적힌 것만 간다.**

        {프리픽스}_LOCAL_SCOPES=conversation,wake,일상

    ★ **주소와 같은 규칙이다** — 적는 순간 결정이고, 결정은 보이는 자리에
      있어야 한다. 되돌리는 법은 그 줄을 지우는 것이다.

    ★ **여기 한 군데서만 읽는다.** 유나와 예나가 각자 자기 `agent.py` 에서
      같은 파싱을 하고 있었다(2026-09-01 에 합쳤다). 자리 이름을 쉼표로 가르는
      규칙이 두 벌이면 그중 하나가 언젠가 낡는다.

    `default` 는 **아무것도 안 적혔을 때**의 값이다. 유나에게는 일상이 이미
    로컬이던 이력이 있어서 그 자리를 기본값으로 넘긴다 — 배포하다 만 상태에서
    돌던 것이 조용히 클라우드로 돌아가지 않게.
    """
    raw = env.get("LOCAL_SCOPES")
    if raw is None:
        return default
    return frozenset(s.strip() for s in raw.split(",") if s.strip())


def switched(frm: str, to: str, why: str = "") -> None:
    """갈아탄 것을 **반드시 한 줄 남긴다.**

    ★ 조용히 갈아타면 아낀 줄 알았던 요금이 그대로고, 그걸 몇 주 뒤에 안다.
      갈아타는 것 자체는 옳은 동작이고, 나쁜 것은 갈아탄 줄 모르는 것이다.

    ★ 부르는 데가 셋이다 — 스펙에 적힌 `fallback`(`runner._client_for`), 자리별로
      고르는 쪽, 그리고 **도중에** 꺼졌을 때(둘 다 유나·예나 `agent.py`).
      규칙이 하나라 자리도 하나다.
    """
    # 받침을 보고 조사를 고른다. 이 줄은 사람이 읽는 자리라 "클라우드 으로" 나
    # "anthropic 로" 가 나오면 눈에 걸린다. ㄹ 받침은 '로' 를 쓰고, 어댑터
    # 이름은 로마자라 끝소리가 홀소리인지로 가른다(gemini 로 / anthropic 으로).
    끝 = to[-1].lower() if to else ""
    if "가" <= 끝 <= "힣":
        조사 = "로" if (ord(끝) - 0xAC00) % 28 in (0, 8) else "으로"
    else:
        조사 = "로" if 끝 in "aeiouy" else "으로"
    print(f"  ↪ {frm} 이 안 열려서 {to} {조사} 간다" + (f" ({why})" if why else ""),
          file=sys.stderr)


def available() -> bool:
    """**떠 있나만 본다.** 키가 아니라 프로세스라, 여기서 묻는 것이
    다른 어댑터와 다르다 — 열려 있는 포트인지 한 번 두드린다.

    ★ `check` 는 키 없이도 돌아야 한다. 이건 1초짜리 TCP 연결이고,
      안 열려 있으면 그냥 `False` 다 — 죽지 않는다.
    """
    try:
        u = urllib.parse.urlparse(url())
        with socket.create_connection((u.hostname or "127.0.0.1", u.port or 80), timeout=1):
            return True
    except OSError:
        return False


def _text(content) -> str:
    """앤트로픽 모양의 content 를 글자로 편다."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") if isinstance(b, dict) else str(getattr(b, "text", ""))
            for b in content
        )
    return str(content or "")


def _tools(tools) -> list[dict] | None:
    """앤트로픽 모양의 도구 목록을 OpenAI 모양으로."""
    if not tools:
        return None
    out = []
    for t in tools:
        # 서버 도구(`{"type": "web_search_..."}`)는 스키마가 없다. 로컬에는
        # 그런 게 없으니 조용히 뺀다 — 보내면 서버가 400 을 낸다.
        if not t.get("name") or not isinstance(t.get("input_schema"), dict):
            continue
        out.append(
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description") or "",
                    "parameters": t["input_schema"],
                },
            }
        )
    return out or None


def _blocks(content):
    """앤트로픽 content 를 블록 목록으로. 글 하나면 한 칸짜리."""
    if isinstance(content, list):
        return content
    return [{"type": "text", "text": _text(content)}]


def _kind(b) -> str:
    return b.get("type", "") if isinstance(b, dict) else getattr(b, "type", "")


def _turns(messages) -> list[dict]:
    """루프가 쌓아 온 것을 OpenAI 대화로 옮긴다.

    ★ **도구 결과는 따로 선 한 턴이다**(`role: "tool"`). 예전처럼 글로 펴면
      요청은 그대로 200 이고, 모델은 자기가 부른 도구가 무엇을 냈는지 모른 채
      답한다 — 오류가 아니라 **조용한 헛소리**로 나온다.
    """
    chat: list[dict] = []
    for m in messages:
        role, blocks = m["role"], _blocks(m.get("content"))

        결과 = [b for b in blocks if _kind(b) == "tool_result"]
        if 결과:
            # 도구 결과만 담긴 턴이다. 부른 것마다 한 턴씩 낸다.
            for b in 결과:
                chat.append(
                    {
                        "role": "tool",
                        "tool_call_id": b.get("tool_use_id") or "",
                        "content": _text(b.get("content")),
                    }
                )
            남은 = [b for b in blocks if _kind(b) != "tool_result"]
            if not 남은:
                continue
            blocks = 남은

        부름 = [b for b in blocks if _kind(b) == "tool_use"]
        글 = _text([b for b in blocks if _kind(b) == "text"])
        turn: dict = {"role": role, "content": 글}
        if 부름:
            turn["tool_calls"] = [
                {
                    "id": getattr(b, "id", "") or (b.get("id") if isinstance(b, dict) else ""),
                    "type": "function",
                    "function": {
                        "name": getattr(b, "name", "")
                        or (b.get("name") if isinstance(b, dict) else ""),
                        "arguments": json.dumps(
                            getattr(b, "input", None)
                            if not isinstance(b, dict)
                            else b.get("input") or {},
                            ensure_ascii=False,
                        ),
                    },
                }
                for b in 부름
            ]
        chat.append(turn)
    return chat


def _called(msg) -> list[ToolUseBlock]:
    """답에 실려 온 도구 호출들."""
    out = []
    for i, c in enumerate(msg.get("tool_calls") or []):
        fn = c.get("function") or {}
        raw = fn.get("arguments")
        try:
            args = json.loads(raw) if isinstance(raw, str) and raw.strip() else (raw or {})
        except ValueError:
            # ★ **인자를 못 읽으면 빈 것으로 부른다.** 여기서 죽으면 그 판이
            #   통째로 끝난다. 작은 모델은 인자를 어긋나게 낼 때가 있고,
            #   그건 루프가 도구 쪽에서 받아 낼 수 있는 종류의 고장이다.
            args = {}
        if not isinstance(args, dict):
            args = {}
        out.append(ToolUseBlock(id=c.get("id") or f"call_{i}", name=fn.get("name") or "", input=args))
    return out


class _Messages:
    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint

    def create(self, *, model, max_tokens, system=None, tools=None,
               messages, **extra) -> Response:
        chat = []
        if system:
            chat.append({"role": "system", "content": _text(system)})
        chat += _turns(messages)

        body = {
            "model": model or DEFAULT_MODEL,
            "messages": chat,
            "max_tokens": max_tokens,
            # ★ 낮게 잡는다. 여기가 하는 일은 **정해진 모양의 JSON 하나**를
            #   내는 것이고, 그 자리에서 다양성은 값이 아니라 고장이다.
            "temperature": float(extra.get("temperature", 0.2)),
            "stream": False,
        }
        spec = _tools(tools)
        if spec:
            body["tools"] = spec
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                d = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # ★ **서버가 이유를 말해 준 것이다.** 413 이면 "이 카드에 안
            #   들어간다" 다 — 그 말을 삼키고 "안 떠 있다" 로 바꾸면, 부르는
            #   쪽이 왜 되돌아갔는지 영영 모른다.
            try:
                왜 = json.loads(e.read().decode("utf-8")).get("error") or ""
            except Exception:  # noqa: BLE001
                왜 = ""
            raise LocalUnavailable(
                f"로컬 모델이 거절했다({e.code}): {왜 or e.reason}"
            ) from None
        except urllib.error.URLError as e:
            raise LocalUnavailable(
                f"로컬 모델에 못 붙었다({self.endpoint}): {e.reason}. "
                "deploy/serve_local.py 나 llama.cpp 서버가 떠 있나"
            ) from None
        except (ValueError, UnicodeDecodeError) as e:
            raise LocalUnavailable(f"로컬 모델의 답을 못 읽었다: {e}") from None

        choices = d.get("choices") or []
        if not choices:
            raise LocalUnavailable(f"로컬 모델이 빈 답을 냈다: {str(d)[:200]}")
        msg = choices[0].get("message") or {}
        u = d.get("usage") or {}
        부름 = _called(msg)
        블록: list = []
        if msg.get("content"):
            블록.append(TextBlock(text=msg["content"]))
        블록 += 부름
        if not 블록:
            블록 = [TextBlock(text="")]
        # OpenAI 는 "stop"/"length"/"tool_calls", 루프는 앤트로픽 말을 본다.
        # ★ **부른 것이 있으면 그게 이유다.** `finish_reason` 을 "stop" 으로
        #   내면서 tool_calls 를 같이 싣는 서버가 있다 — 실린 것을 믿는다.
        끝 = choices[0].get("finish_reason")
        return Response(
            content=블록,
            stop_reason=(
                "tool_use" if 부름 else "max_tokens" if 끝 == "length" else "end_turn"
            ),
            usage=Usage(
                input_tokens=int(u.get("prompt_tokens") or 0),
                output_tokens=int(u.get("completion_tokens") or 0),
            ),
        )


class LocalClient:
    """`Client` 프로토콜 하나. `messages.create(...)` 뿐이다."""

    def __init__(self, endpoint: str = "") -> None:
        self.messages = _Messages(endpoint or url())


def client(api_key: str | None = None):
    """`api_key` 는 안 쓴다 — 키가 없는 것이 이 어댑터의 요점이다."""
    return LocalClient()
