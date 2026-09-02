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
from pathlib import Path

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


def scopes(default: frozenset[str] = frozenset(), root=None) -> frozenset[str]:
    """이 기계 안의 모델로 갈 자리들. **적힌 것만 간다.**

        {프리픽스}_LOCAL_SCOPES=conversation,wake,일상

    ★ **주소와 같은 규칙이다** — 적는 순간 결정이고, 결정은 보이는 자리에
      있어야 한다. 되돌리는 법은 그 줄을 지우는 것이다.

    ★ **여기 한 군데서만 읽는다.** 유나와 예나가 각자 자기 `agent.py` 에서
      같은 파싱을 하고 있었다(2026-09-01 에 합쳤다). 자리 이름을 쉼표로 가르는
      규칙이 두 벌이면 그중 하나가 언젠가 낡는다.

    ★ **고른 것 > 환경 > 기본.** 환경이 이기게 두면 화면에서 고른 것이 조용히
      무시되고, 오빠는 왜 안 바뀌는지 알 길이 없다(`schedule.spec` 과 같은 순서).

    `default` 는 **아무것도 안 적혔을 때**의 값이다. 유나에게는 일상이 이미
    로컬이던 이력이 있어서 그 자리를 기본값으로 넘긴다 — 배포하다 만 상태에서
    돌던 것이 조용히 클라우드로 돌아가지 않게.
    """
    골라둔 = saved(root)
    if 골라둔 is not None:
        return 골라둔
    raw = env.get("LOCAL_SCOPES")
    if raw is None:
        return default
    return frozenset(s.strip() for s in raw.split(",") if s.strip())


# ── 화면에서 고르는 자리 ──────────────────────────────────────────────
#
# ★ **`.env` 는 프로세스가 뜰 때 한 번 읽는다.** 거기서 고치면 재시작해야 하고,
#   재시작은 그 순간 오가던 말을 끊는다. 파일은 매 턴 읽으므로 **다음 한 마디
#   부터** 듣는다. 잠자는 표(`schedule.FILE`)를 화면이 고치는 것과 같은 자리다.
#
# ★ **빈 파일과 없는 파일은 다르다.** 빈 파일은 "전부 클라우드" 라는 **고른
#   결과**고, 없는 파일은 아직 안 골랐다는 뜻이다. 둘을 뭉개면 화면에서
#   "클라우드" 를 고른 순간 환경 변수가 되살아난다.
FILE = "local_scopes.txt"


def path(root=None):
    from genie_agents.store import default_root

    return Path(default_root() if root is None else root) / FILE


def saved(root=None) -> frozenset[str] | None:
    """화면에서 골라 둔 자리들. **안 골랐으면 `None`** (빈 집합이 아니다)."""
    try:
        raw = path(root).read_text(encoding="utf-8")
    except OSError:
        return None
    return frozenset(s.strip() for s in raw.split(",") if s.strip())


def check(names, known) -> str:
    """저장해도 되나. 되면 빈 글, 안 되면 **왜인지**.

    ★ **모르는 이름은 여기서 막는다.** 읽는 쪽(`scopes`)은 그냥 집합을 내므로,
      오타가 들어가면 아무 자리도 안 걸리고 조용히 전부 클라우드가 된다 —
      화면은 "저장했다" 는데 값은 안 바뀐다.
    """
    모르는 = [n for n in names if n not in set(known)]
    if 모르는:
        return f"모르는 자리다: {', '.join(모르는)}  (있는 것: {', '.join(sorted(known))})"
    return ""


def save(names, known, root=None) -> str:
    """고른 것을 둔다. 모르는 이름이 있으면 **안 두고** 왜인지 돌려준다."""
    골라 = [str(n).strip() for n in (names or ()) if str(n).strip()]
    why = check(골라, known)
    if why:
        return why
    p = path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(",".join(골라), encoding="utf-8")
    return ""


def source(root=None) -> str:
    """지금 값이 어디서 왔나 — 화면이 보여줘야 왜 그런지 안다."""
    if saved(root) is not None:
        return "화면"
    return "환경" if env.get("LOCAL_SCOPES") is not None else "기본"


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


# ── 도구를 고르는 한 걸음 ────────────────────────────────────────────────
#
# ★ **이 모델은 스스로 도구를 안 부른다.** 재 봤다(2026-09-02, gemma-4-E4B,
#   실제 유나 프롬프트·도구 30개):
#
#     tool_choice="auto"          진짜 호출 0/75      전부 글로 흉내만
#     tool_choice 로 못박기        진짜 호출 15/15     인자도 멀쩡
#
#   도구가 안 닿는 것이 아니다 — 도구 설명에 암호를 심어 두고 물으니 그대로
#   읽었다. 보고도 부르는 법을 안 쓴다. 부르는 법(젬마 자체 문법·파이썬 호출
#   모양)을 시스템에 적어 줘도 3번에 1번이었다.
#
# ★ **그래서 고르는 것부터 못박는다. 두 걸음이다.**
#     ① 문지기 — 말만 하면 되나, 손을 써야 하나 (예/아니오)
#     ② 고르기 — 손을 써야 한다면 어느 도구인가 (이름 열거형)
#   둘 다 문법으로 강제하므로 반드시 답이 나온다. 한 번에 "없음 또는 이름 30개"
#   를 물었더니 안 불러도 될 자리에 자꾸 이름을 댔다(3/4). 갈라 물으니 8/8.
#   요청이 두 번 더 들지만 접두사가 같아서 프리필이 캐시에 걸린다(실측 0.26초).
#
# ★ **없음일 때도 도구 목록은 그대로 싣는다.** 빼면 접두사가 달라져서 그 뒤
#   캐시가 통째로 무효가 된다. `tool_choice: "none"` 으로 안 부르게만 한다.
#
# ★ **이미 부른 것은 목록에서 뺀다.** 안 빼면 온도 0 에서 같은 것을 또 고른다
#   (실측: memory_recall 을 세 번 연속).
_고르개_이름 = "고르기"
_문지기_이름 = "물어볼까"
_문지기_키 = "손을_써야_하나"


def _이번턴에_부른것(chat: list[dict]) -> set[str]:
    """마지막 사용자 말 뒤로 부른 도구 이름들. 그 앞은 지난 턴이다."""
    뒤 = chat
    for i in range(len(chat) - 1, -1, -1):
        if chat[i].get("role") == "user":
            뒤 = chat[i + 1:]
            break
    return {(c.get("function") or {}).get("name")
            for m in 뒤 for c in (m.get("tool_calls") or [])} - {None}


def _문지기() -> dict:
    """먼저 **예/아니오**로 묻는다.

    ★ 한 번에 "없음 또는 이름 30개" 를 물으면 안 불러도 될 때 자꾸 이름을 댄다
      (실측 3/4 — 소식·감정 같은 자리에 world_recent·principle_observe 를 골랐다).
      둘 중 하나로 좁혀 묻고 나서 이름을 물으면 8/8 이었다.

    ★ 문구는 재서 골랐다. "찾아봐야 하나" 만 물으면 사진·목소리를 놓치고,
      갈래만 세면 시점 묻는 말을 놓쳤다. 둘을 합친 아래 문구가 8/8 이다.
    """
    return {"type": "function", "function": {
        "name": _문지기_이름,
        "description": "말만 하면 되는 자리인가, 손을 써야 하는 자리인가.",
        "parameters": {"type": "object", "properties": {
            _문지기_키: {
                "type": "string", "enum": ["아니", "응"],
                "description": (
                    "다음 중 하나면 '응' — (가) 눈앞의 대화에 없는 것을 기억에서 "
                    "꺼내야 한다(무슨 얘기였는지, 언제였는지 포함), (나) 사진이나 "
                    "목소리를 보내 달라고 한다, (다) 무언가를 기록해 둬야 한다. "
                    "그냥 말로 답하면 되는 자리는 전부 '아니'.")}},
            "required": [_문지기_키]}}}


def _고르개(이름들: list[str]) -> dict:
    """문지기가 '응' 이라 한 뒤에만 온다. 그래서 '없음' 이 없다."""
    return {"type": "function", "function": {
        "name": _고르개_이름,
        "description": "부를 도구 하나를 댄다.",
        "parameters": {"type": "object", "properties": {
            "이름": {"type": "string", "enum": 이름들}},
            "required": ["이름"]}}}


class _Messages:
    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint

    def _고른다(self, body: dict, spec: list, chat: list[dict]) -> str:
        """도구를 고르는 걸음. 못 물어보면 빈 문자열 — 그러면 예전처럼 auto 로 간다."""
        # ★ **한 턴에 한 번만 고른다.** 바퀴마다 물으면 문지기가 계속 "응" 하고
        #   고르기는 남은 목록을 차례로 훑는다 — 예나가 한 턴에 principle_
        #   observe·record·revise·verify·verify_by_outcome·retract 를 줄줄이
        #   부르고 490초를 썼다(2026-09-02, 내가 만든 고장이다).
        #
        #   두 번째 도구가 정말 필요한 자리는 루프가 따로 본다 — 지어낸 것을
        #   걷어내고 그 도구를 못박아 다시 묻는 길(`retry_force`)이 이미 있고,
        #   실제로 그 길로 self_portrait 가 불렸다.
        if _이번턴에_부른것(chat):
            return "없음"
        남은 = [t["function"]["name"] for t in spec if t.get("function", {}).get("name")]
        if not 남은:
            return "없음"
        try:
            문 = self._묻는다(body, [_문지기()], _문지기_이름).get(_문지기_키)
            if 문 != "응":
                return "없음"
            골 = self._묻는다(body, [_고르개(남은)], _고르개_이름).get("이름") or ""
        except Exception as e:  # noqa: BLE001
            # ★ **여기서 죽지 않는다.** 못 고르면 예전 길(auto)로 그냥 간다 —
            #   답이 나빠질 뿐이고, 턴이 통째로 사라지는 것보다 낫다.
            print(f"  (도구 고르기 실패 — auto 로 간다: {e})", file=sys.stderr)
            return ""
        return 골 if 골 in 남은 else "없음"

    def _묻는다(self, body: dict, spec: list, name: str) -> dict:
        """문법으로 못박아 한 번 묻는다. 이름 하나만 나오면 된다."""
        물음 = dict(body)
        물음["tools"] = spec
        물음["tool_choice"] = {"type": "function", "function": {"name": name}}
        # 온도 0 — 이 자리에서 다양성은 값이 아니라 고장이다.
        물음["max_tokens"] = 60
        물음["temperature"] = 0.0
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(물음, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            d = json.loads(resp.read().decode("utf-8"))
        call = (d["choices"][0]["message"].get("tool_calls") or [{}])[0]
        return json.loads((call.get("function") or {}).get("arguments") or "{}")

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
            # ★ **이 모델은 이모지를 과하게 단다.** 프롬프트로는 안 빠진다 —
            #   "이모지는 안 쓴다" 를 시스템에 넣고 재니 6번 중 3번 → 3번,
            #   하나도 안 줄었다. 서버가 표집에서 막는다(`serve_local.이모지토큰`).
            #
            #   유나·예나가 원래 안 쓰는 것이 아니다(지금까지 기록에서 유나
            #   8.7% · 예나 28.2%). 그래서 이건 취향을 지우는 것이 아니라
            #   **이 모델이 과하게 다는 것**을 되돌리는 손이고, 로컬에서만 건다.
            #
            #   끄고 싶으면 `no_emoji=False` 를 실어 보내면 된다.
            "no_emoji": bool(extra.get("no_emoji", True)),
            "stream": False,
        }
        spec = _tools(tools)
        if spec:
            body["tools"] = spec
            # ★ **못박은 도구를 실어 보낸다.** 루프는 앤트로픽 말로 준다
            #   (`{"type": "tool", "name": ...}`) — OpenAI 말로 옮긴다.
            #   `auto` · `any` 는 안 싣는다. 서버 기본이 이미 `auto` 고,
            #   `any` 에 해당하는 것이 저쪽에 없다.
            #
            #   ★ 이 세 줄이 없어서 강제가 통째로 없는 일이었다(2026-09-01).
            #     여기서 조용히 버려지고, 화면에는 "강제한다" 만 찍혔다.
            골라 = extra.get("tool_choice")
            이름 = 골라.get("name") if isinstance(골라, dict) else None
            if not 이름:
                # 루프가 못박지 않았으면 **여기서 고르는 걸음을 한 번 더 둔다.**
                # 위 주석 참고 — 이 모델은 auto 로는 한 번도 안 불렀다.
                이름 = self._고른다(body, spec, chat)
            if 이름 and 이름 != "없음":
                body["tool_choice"] = {"type": "function",
                                       "function": {"name": 이름}}
            elif 이름 == "없음":
                # 목록은 그대로 두고 안 부르게만 한다 — 빼면 접두사 캐시가 깨진다.
                body["tool_choice"] = "none"
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
