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

  tools            **넘겨도 안 쓴다.** 이 어댑터가 서는 자리는 추출이고,
                   거기는 도구를 안 부르고 JSON 하나를 받는 자리다
                   (`docs/wiring.md` 5절). 도구 루프를 로컬로 내리려면
                   그때 여기에 tool_calls 를 붙여야 한다 — 지금은 없다
  cache_control    프롬프트 캐시가 없다. 붙여 보내도 그냥 지나간다
  서버 도구        없다
  cache 토큰 수    `Usage` 의 캐시 칸은 항상 0 이다

━━ 이 어댑터가 서는 자리 ━━

★ 팀원의 글이 기계 밖으로 안 나가게 하려고 있는 것이다. 그래서 **주소가
  기본값으로 로컬호스트**이고, 다른 데를 가리키려면 그 값을 손으로 적어야 한다.
  적는 순간 그건 결정이 되고, 결정은 보이는 자리에 있어야 한다.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request

from .. import env
from .base import Response, TextBlock, Usage

DEFAULT_URL = "http://127.0.0.1:8080/v1/chat/completions"
DEFAULT_MODEL = "로컬-모델"
TIMEOUT = 300.0
"""4B 를 6GB 에서 돌리면 한 묶음에 십수 초가 걸린다. 넉넉히 둔다 —
하루 열 번 안쪽으로 도는 물건이라 기다리는 값이 싸다."""


class LocalUnavailable(RuntimeError):
    """서버가 안 떠 있거나 답이 이상하다."""


def url() -> str:
    return env.get("LOCAL_URL") or DEFAULT_URL


def default_model(fast: bool = False) -> str:
    return env.get("LOCAL_MODEL") or DEFAULT_MODEL


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


class _Messages:
    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint

    def create(self, *, model, max_tokens, system=None, tools=None,
               messages, **extra) -> Response:
        chat = []
        if system:
            chat.append({"role": "system", "content": _text(system)})
        for m in messages:
            chat.append({"role": m["role"], "content": _text(m.get("content"))})

        body = {
            "model": model or DEFAULT_MODEL,
            "messages": chat,
            "max_tokens": max_tokens,
            # ★ 낮게 잡는다. 여기가 하는 일은 **정해진 모양의 JSON 하나**를
            #   내는 것이고, 그 자리에서 다양성은 값이 아니라 고장이다.
            "temperature": float(extra.get("temperature", 0.2)),
            "stream": False,
        }
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                d = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise LocalUnavailable(
                f"로컬 모델에 못 붙었다({self.endpoint}): {e.reason}. "
                "llama.cpp 서버가 떠 있나"
            ) from None
        except (ValueError, UnicodeDecodeError) as e:
            raise LocalUnavailable(f"로컬 모델의 답을 못 읽었다: {e}") from None

        choices = d.get("choices") or []
        if not choices:
            raise LocalUnavailable(f"로컬 모델이 빈 답을 냈다: {str(d)[:200]}")
        msg = choices[0].get("message") or {}
        u = d.get("usage") or {}
        return Response(
            content=[TextBlock(text=msg.get("content") or "")],
            # OpenAI 는 "stop"/"length", 루프는 앤트로픽 말을 본다.
            stop_reason="max_tokens" if choices[0].get("finish_reason") == "length"
                        else "end_turn",
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
