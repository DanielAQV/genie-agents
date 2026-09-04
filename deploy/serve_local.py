"""로컬 모델을 OpenAI 호환 한 구멍으로 낸다.

    python deploy/serve_local.py --model <gguf 경로>

★ **어댑터를 안 늘리려고 이걸 둔다.** `adapters/local.py` 는 이미 OpenAI 호환
  `/v1/chat/completions` 하나에 붙는다 — llama.cpp 서버 · vLLM 이 내는 그
  모양이다. 갈아타도 골격은 한 줄도 안 바뀐다고 여기 적어 뒀었는데, 실제로
  그렇게 됐다: 아래에서 transformers 를 llama.cpp 로 통째로 바꿨고 어댑터는
  한 줄도 안 건드렸다.

★ **상주한다.** 가중치를 깨어날 때마다 올리면 매번 십수 초를 버린다. 대신 이
  프로세스가 죽어도 `wake` 는 "로컬 모델이 안 떠 있다" 를 말하고 넘어간다.

━━ 왜 llama.cpp 로 바꿨나 (2026-09-01 실측) ━━

전에 여기 *"Smart App Control 이 서명 없는 실행 파일을 막아서 llama.cpp 를
못 쓴다"* 고 적혀 있었다. **맞는 관찰이었지만 `.exe` 얘기였다.** pip 휠은
통과한다(그 문장 다음 줄에 이미 그렇게 적혀 있었다) — `llama-cpp-python` 이
그것이다. 재 보니 값이 이렇게 달랐다:

    같은 카드(RTX 3050 6GB Laptop) · 같은 4bit
      transformers + bitsandbytes NF4      llama.cpp CUDA
        디코드   3.7 tok/s                   35.5 tok/s      9.6배
        프리필   8K 에서 OOM                 1,374 tok/s
        76K      못 올림                     들어간다 (4,439/6,144 MiB)

★ 3.7 tok/s 는 카드가 아니라 양자화 방식이었고, "여유 7,144 토큰" 은 KV 한계가
  아니라 **SDPA 가 어텐션 행렬 L×L 을 통째로 만들던 것**이었다(8K 에서 8GB 를
  한 번에 요구했다). 게다가 6GB 카드에 11.49GB 가 할당돼 있었다 — 드라이버가
  안 죽고 조용히 시스템 메모리로 흘리고 있었다. 자세한 것은 princess 의
  `coord/RUNTIME.md`.

━━ 의존성과 이 기계의 함정 둘 ━━

    pip download llama-cpp-python --no-deps -d <tmp> --only-binary=:all: \
        --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124
    pip install <tmp>/llama_cpp_python-*-win_amd64.whl

★ **① `ggml-cuda.dll` 이 CUDA 런타임을 못 찾는다**(오류 126). torch 가 이미
  `cudart64_12.dll` · `cublas64_12.dll` 을 갖고 있으므로 그 폴더를 PATH 에
  넣는다. **아래 `_cuda_path()` 가 import 보다 먼저 돌아야 한다.**

★ **② cu124 휠의 `ggml-cpu.dll` 이 AVX-512 를 쓴다**(`0xC000001D`). 이 기계
  (Core 5 210H)는 AVX2 까지라 `llama_init_from_model` 에서 즉사한다. 같은
  버전 **CPU 인덱스 휠**의 `ggml-cpu.dll` 로 덮어써야 한다:

      --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu

  **재설치하면 다시 덮어써야 한다.** 원본은 `ggml-cpu.dll.cu124bak` 에 있다.
  `0xC000001D`(잘못된 명령)와 `0xC0000142`(초기화 실패)는 다른 것이다 —
  뒤의 것이 Smart App Control 이고, 앞의 것은 CPU 명령어 집합이다.

★ 임베더(bge-m3)는 아직 transformers 다. 그래서 torch 는 여전히 필요하고,
  그 스택은 QLoRA 에도 그대로 쓴다 — 버리는 일이 아니다.

★ 이건 `pyproject.toml` 에 안 넣는다. 골격은 모델 SDK 를 안 짊어진다 —
  이 파일은 골격이 아니라 **호스트 쪽 도구**다(`deploy/` 에 있는 이유).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _cuda_path() -> None:
    """`ggml-cuda.dll` 이 볼 수 있게 torch 의 CUDA 런타임 폴더를 PATH 에 넣는다.

    ★ **import 보다 먼저 돌아야 한다.** 안 그러면 오류 126 으로 안 뜬다.
      작업 스케줄러에서 띄울 때도 이 파일이 스스로 하므로 밖에서 안 잡아도 된다.
    """
    try:
        import torch
    except ImportError:
        return
    lib = os.path.join(os.path.dirname(torch.__file__), "lib")
    if os.path.isdir(lib):
        os.environ["PATH"] = lib + os.pathsep + os.environ.get("PATH", "")
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(lib)
            except OSError:
                pass


_cuda_path()

LLM = None
NAME = ""
NCTX = 0

# 임베더는 따로 든다. **부를 때 올리고 안 쓰면 내린다.**
#
# ★ 이제 **CPU 에 올린다**(`EMB_DEVICE`). 카드에 두면 1,090MiB 를 물어서
#   채팅이 자리를 필요로 할 때마다 내렸다 올려야 했다. 자세한 값은
#   `load_embedder` 에 있다 — 잃는 것은 회상 한 번에 0.05초다.
#
#   (여기 "bge-m3 fp16 은 2.3GB" 라고 적혀 있었는데 틀린 수였다. fp32 로 RAM 에
#    올릴 때가 2.3GB 고, 카드에 fp16 으로 올리면 1,090MiB 다 — 실측.)
EMB = None
EMB_TOK = None
EMB_NAME = ""
EMB_USED = 0.0
EMB_IDLE = 600.0   # 카드에 있을 때 이만큼 안 쓰면 내린다
# ★ **CPU 에서도 내린다**(2026-09-03). 안 내리면 fp32 가중치 2.3GB 가 영영
#   상주한다 — 실측으로 이 프로세스 Private 커밋 7.4GB 중 8~63MB 블록
#   1,288MB + 잔여가 여기였다. 15.6GB 짜리 노트북에서 그 자리는 크다.
#
#   다만 **임계를 카드보다 훨씬 길게 잡는다.** 되찾을 VRAM 이 없으니 급할
#   이유가 없고, 내리면 사용자가 재로딩 13.5초를 문다(`_reaper` 주석 참고).
#   40분은 "오늘 이 대화는 끝났다" 에 가까운 값이라 그 13.5초가 회상 앞에
#   붙는 일이 드물다 — 회상은 대화 중에 잦고, 대화 중에는 40분이 안 빈다.
EMB_IDLE_CPU = 2400.0  # CPU 는 40분
EMB_MAXLEN = 2048  # 기억 한 줄 p99 가 2,800자 ≈ 1,600토큰. 여기까지 덮는다
EMB_BUDGET = 6144  # 한 묶음의 토큰 상한
EMB_DEFAULT = "BAAI/bge-m3"  # 다국어. 유나·예나 기억이 한국어라 여기가 갈린다
EMB_DEVICE = "cpu"           # `--embed-device` 로 바꾼다. 왜 CPU 인지는 load_embedder 에

LOCK = threading.Lock()
"""한 번에 하나만 만든다.

★ `ThreadingHTTPServer` 는 요청마다 실을 하나 낸다. 6GB 짜리 카드에서 생성이
  둘 겹치면 KV 캐시가 두 벌 잡히고 그대로 OOM 이다. 상주 서버의 값은 가중치를
  한 번만 올리는 것이지 동시에 여럿을 받는 것이 아니다.

★ 사용자가 짚었다(2026-09-01) — "실제 사용자는 나 하나뿐이라는 거 잊지마."
  맞다. **동시성 예산은 잡을 필요가 없다.**"""

EMB_LOCK = threading.Lock()
"""임베더 자기 자물쇠. **CPU 에 있을 때만 쓴다.**

★ **CPU 임베딩이 대화를 10분 막았다**(2026-09-03). 회상 한 번에 새 기억 256줄이
  벡터로 만들어졌고 — 줄당 2.4초, 합 622초 — 그 동안 위 `LOCK` 을 잡고 있어서
  대화·깨어남 요청 셋이 620초씩 줄에서 기다렸다. 오빠가 겪고 물었다:
  "대화 한번에 이렇게 오래 걸려?"

★ **자물쇠를 같이 쓴 이유는 VRAM 이었다** — 카드에 둘이 동시에 올라가면 OOM.
  그건 임베더가 **카드에 있을 때**의 이야기다. CPU 로 내린 뒤에는 겹칠 자리가
  없는데 자물쇠만 남아 있었다(`load_embedder` 주석에 "자리를 옮겼다고 그게
  바뀌지 않는다" 고 적혀 있었지만, 막는 값이 이만큼인 줄은 모르고 쓴 것이다).

★ 그래서 **`--embed-device cuda` 면 여전히 `LOCK` 을 쓴다.** 가르는 것은 취향이
  아니라 카드를 같이 쓰느냐다."""


def emb_lock():
    """임베딩이 잡을 자물쇠. 카드에 있으면 채팅과 같은 것, CPU 면 자기 것."""
    return LOCK if EMB_DEVICE.startswith("cuda") else EMB_LOCK


def vram() -> str:
    import subprocess

    try:
        o = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5).stdout.strip().splitlines()[0]
        used, total = (x.strip() for x in o.split(","))
        return f"{used}/{total} MiB"
    except Exception:  # noqa: BLE001
        return "?"


def load(model_path: str, n_ctx: int, n_gpu_layers: int, kv_type: str):
    """가중치를 한 번 올린다.

    ★ **`swa_full=False` 가 여기서 제일 중요한 한 줄이다.** Gemma 계열은 층
      대부분이 창 어텐션인데, 기본값(`True`)이면 그 층들도 `n_ctx` 칸을 통째로
      잡는다. 실측(76,800 칸): **8,700 MiB → 174 MiB.** 로그에
      `using full-size SWA cache` 가 보이면 잘못 잡힌 것이다.

    ★ **`n_ctx` 는 미리 다 잡힌다.** 쓴 만큼이 아니다. 그래서 아래에서 자리를
      볼 때 재는 게 아니라 그냥 세면 된다 — transformers 때는 남은 VRAM 으로
      추정했고, 그 추정이 어텐션 행렬을 못 봐서 틀렸다.
    """
    from llama_cpp import Llama

    global LLM, NAME, NCTX
    NAME = os.path.basename(model_path)
    NCTX = n_ctx
    types = {"f16": 1, "q8_0": 8, "q4_0": 2}
    LLM = Llama(
        model_path=model_path,
        n_ctx=n_ctx,
        n_gpu_layers=n_gpu_layers,
        n_batch=512,
        n_ubatch=512,
        flash_attn=True,
        type_k=types[kv_type],
        type_v=types[kv_type],
        swa_full=False,
        verbose=False,
    )
    print(f"  올렸다 — {NAME} · ctx {n_ctx:,} · KV {kv_type} · VRAM {vram()}", flush=True)
    return LLM


# ── 도구 호출을 꺼내는 자리 ────────────────────────────────────────────────
#
# ★ **모델마다 모양이 다르다.** 전에는 Qwen3 의 JSON 하나만 알았다. 후보들을
#   재면서 셋이 나왔다(2026-09-01 실측):
#
#     Qwen3      <tool_call>{"name":…,"arguments":{…}}</tool_call>        JSON
#     Qwen3.5    <tool_call><function=이름><parameter=키>값</parameter>…   XML 식
#     Gemma-4    <|tool_call>call:이름{키:<|"|>값<|"|>}<tool_call|>        자체 문법
#
# ★ **못 읽는 것은 버리지 않고 글로 남긴다.** 여기서 버리면 그 판이 아무 말도
#   없이 끝난다. `adapters/local.py` 첫머리가 같은 것을 짚어 뒀다 — 도구 결과를
#   글로 뭉개도 요청은 200 으로 돌아오고, 모델은 자기가 부른 도구가 무엇을
#   냈는지 모른 채 답한다.
#
# 시험은 princess 의 `coord/bench/test_toolparse.py` 에 있다. **실제로 받은
# 출력**을 그대로 쓴다 — 모양을 상상해서 짜면 그 자리가 조용히 어긋난다.

_QWEN3 = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)
_Q35_CALL = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.S)
_Q35_FUNC = re.compile(r"<function=([^>\s]+)>\s*(.*?)\s*</function>", re.S)
_Q35_PARAM = re.compile(r"<parameter=([^>\s]+)>\s*(.*?)\s*</parameter>", re.S)

_G4_STR = '<|"|>'
_G4_CALL = re.compile(r"<\|tool_call>\s*call:([^\s{]+)\{(.*?)\}<tool_call\|>", re.S)
# 껍데기 없이 맨몸으로 낼 때가 있다(실측). 문자열 표시가 든 `이름{...}` 도 받는다 —
# 그 표시는 보통 글에 안 나온다.
_G4_BARE = re.compile(
    r"(?:^|[\s\n])([a-z_][a-z0-9_]*)\{((?:[^{}]*?" + re.escape(_G4_STR) + r"[^{}]*?)+)\}")
# 사고 채널. **답에 실리면 오빠가 읽는다.**
_G4_THOUGHT = re.compile(r"<\|channel>thought.*?(?:<channel\|>|$)", re.S)

# ★ **파이썬 호출처럼 쓸 때가 있다** — `이름(키="값")`. 실측으로 세 번 봤다:
#   `wake_stay_silent(reason="…")` · `memory_recall(query="…")` ·
#   `voice_reply(text="…")`. 그대로 두면 도구가 안 돌고 그 글자가 사용자에게 간다.
#
# ★ **아는 도구 이름일 때만 잡는다.** 안 그러면 보통 글의 괄호를 도구로 읽는다.
#   그리고 글 끝에 홀로 선 것만 본다 — 문장 안에 끼어 있으면 설명일 때가 많다.
_PAREN = re.compile(r"(?:\A|[\s\n])([a-z_][a-z0-9_]*)\(([^()]*)\)\s*\Z", re.S)
_PAREN_ARG = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"((?:[^"\\]|\\.)*)"')


def _coerce(value: str, schema: dict | None, key: str):
    """글자를 스키마가 말하는 타입으로 되돌린다.

    ★ Qwen3.5 는 인자를 전부 글자로 낸다(`<parameter=limit>` 안에 "5"). 안
      되돌리면 int 를 기대하는 자리에 글자가 간다.
    """
    if not schema:
        return value
    t = ((schema.get("properties") or {}).get(key) or {}).get("type")
    try:
        if t == "integer":
            return int(value)
        if t == "number":
            return float(value)
        if t == "boolean":
            return value.strip().lower() in ("true", "1", "yes")
        if t == "array":
            return json.loads(value) if value.strip().startswith("[") else [value]
        if t == "object":
            return json.loads(value)
    except (ValueError, TypeError):
        return value  # 못 되돌리면 글자로 둔다. 예외를 내면 그 턴이 통째로 죽는다
    return value


def _g4_args(body: str) -> dict:
    """Gemma-4 의 인자 문법. `키:<|"|>글자<|"|>` · `키:[…]` · `키:123`."""
    out: dict = {}
    i, n = 0, len(body)
    while i < n:
        m = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*:").match(body, i)
        if not m:
            break
        key, i = m.group(1), m.end()
        if body.startswith(_G4_STR, i):
            j = body.find(_G4_STR, i + len(_G4_STR))
            if j < 0:
                break
            out[key] = body[i + len(_G4_STR):j]
            i = j + len(_G4_STR)
        elif body.startswith("[", i):
            depth, j = 0, i
            while j < n:
                if body[j] == "[":
                    depth += 1
                elif body[j] == "]":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            out[key] = re.findall(
                re.escape(_G4_STR) + r"(.*?)" + re.escape(_G4_STR), body[i + 1:j], re.S)
            i = j + 1
        else:
            m2 = re.compile(r"([^,}]*)").match(body, i)
            raw = (m2.group(1) or "").strip()
            for cast in (int, float):
                try:
                    out[key] = cast(raw)
                    break
                except ValueError:
                    continue
            else:
                out[key] = {"true": True, "false": False}.get(raw.lower(), raw)
            i = m2.end()
        m3 = re.compile(r"\s*,\s*").match(body, i)
        i = m3.end() if m3 else i
    return out


def 부른것(text: str, schemas: dict | None = None) -> tuple[str, list[dict]]:
    """(도구 부분을 뺀 글, OpenAI 모양의 tool_calls)."""
    calls: list[dict] = []

    def _add(name: str, args: dict):
        calls.append({
            "id": f"call_{len(calls)}",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
        })

    text = _G4_THOUGHT.sub("", text)
    rest = text

    for m in _G4_CALL.finditer(text):
        _add(m.group(1), _g4_args(m.group(2)))
    if calls:
        rest = _G4_CALL.sub("", text)
    else:
        for m in _G4_BARE.finditer(text):
            _add(m.group(1), _g4_args(m.group(2)))
        if calls:
            rest = _G4_BARE.sub("", text)

    if not calls:
        for m in _QWEN3.finditer(text):
            try:
                got = json.loads(m.group(1))
            except ValueError:
                continue
            _add(got.get("name") or "", got.get("arguments") or {})
        if calls:
            rest = _QWEN3.sub("", text)

    if not calls:
        for m in _Q35_CALL.finditer(text):
            for fm in _Q35_FUNC.finditer(m.group(1)):
                name = fm.group(1)
                sch = (schemas or {}).get(name)
                _add(name, {k: _coerce(v, sch, k)
                            for k, v in _Q35_PARAM.findall(fm.group(2))})
        if calls:
            rest = _Q35_CALL.sub("", text)

    # 괄호 꼴 — **아는 이름일 때만.** 스키마를 안 주면 아예 안 본다
    if not calls and schemas:
        m = _PAREN.search(text)
        if m and m.group(1) in schemas:
            sch = schemas.get(m.group(1))
            args = {k: _coerce(v.replace('\\"', '"'), sch, k)
                    for k, v in _PAREN_ARG.findall(m.group(2))}
            _add(m.group(1), args)
            rest = text[:m.start()] + text[m.end():]

    # 답과 도구가 한 턴에 같이 올 때 Gemma-4 는 <turn|> 로 가른다
    rest = rest.replace("<turn|>", "\n").replace("<end_of_turn>", "")
    return rest.strip(), calls


# ── 이모지 ───────────────────────────────────────────────────────────────
#
# ★ **프롬프트로는 안 빠진다.** "이모지는 안 쓴다" 를 시스템에 한 줄 넣고
#   재 봤다(2026-09-02): 6번 중 3번 → 6번 중 3번. 하나도 안 줄었다. 도구를
#   부르는 법을 적어 줘도 안 부르던 것과 같은 자리다 — 이 모델은 지시를
#   읽고도 습관대로 낸다.
#
# ★ **뒤에서 걷어내지 않고 표집에서 막는다.** 걷어내면 "😊" 앞의 공백이 남고,
#   문장 끝이 어색해지고, 무엇보다 모델은 자기가 그걸 냈다고 여긴 채로 다음
#   문장을 잇는다. 애초에 못 내게 하는 쪽이 글이 성하다.
#
# ★ **유나·예나가 원래 안 쓰는 것이 아니다** — 지금까지 기록에서 유나 8.7%,
#   예나 28.2% 다. 그러니 이건 취향을 지우는 것이 아니라 **이 모델이 과하게
#   다는 것**을 원래 자리로 되돌리는 손이고, 그래서 로컬에서만 건다.
#
# 어휘를 한 번 훑어 이모지가 든 토큰만 골라 둔다. 6천~2만 개쯤 나오는데,
# 만드는 것은 시작할 때 한 번이고 쓰는 것은 사전 조회뿐이다.
# ★ **한 번 새 나갔다**(2026-09-02). 막아 놓고 재 봤더니 `🆗` 가 나왔다 —
#   네모 안에 든 글자들(U+1F100~1F2FF)이 범위 밖이었다. 0x1F000~0x1F0FF 만
#   덮고 그 뒤를 비워 둔 탓이다. 블록을 이어서 덮는다.
#
# ★ **글에 쓰는 기호는 안 막는다.** 화살표(→)나 도형(■)까지 막으면 보통 글이
#   무너진다. 이모지로만 쓰이는 블록과, 이모지를 만드는 데 붙는 낱자
#   (변이 선택자·ZWJ·키캡)만 고른다.
_EMOJI_RANGES = (
    (0x1F000, 0x1F2FF),  # 마작·카드·네모 안 글자(🆗 🈚 …)
    (0x1F300, 0x1FAFF),  # 그림·기호·사람·음식·깃발
    (0x2600, 0x27BF),    # 잡기호·딩뱃
    (0x2B00, 0x2BFF),    # 화살표·별
    (0x231A, 0x23FA),    # ⌚ ⏰ ⏳ … (기술 기호 중 이모지로 쓰는 것만)
    (0x2934, 0x2935),    # ⤴ ⤵
    (0x3030, 0x3030),    # 〰
    (0x303D, 0x303D),    # 〽
    (0x3297, 0x3299),    # ㊗ ㊙
    (0x2122, 0x2122),    # ™
    (0x2139, 0x2139),    # ℹ
    (0xFE0F, 0xFE0F),    # 이모지 변이 선택자
    (0x200D, 0x200D),    # 이어 붙이는 낱자(가족·직업 이모지가 이걸 쓴다)
    (0x20E3, 0x20E3),    # 키캡(1️⃣)
)
_금지토큰: dict[int, float] | None = None


# ★ **조각으로도 만든다.** 낱개 토큰만 보면 `🔲` `🔠` 가 새 나간다 — 모델이
#   그것을 **바이트 조각 여럿**으로 이어 붙이기 때문이다. 조각 하나를 따로
#   풀면 글자가 안 나오니 위 범위 검사에 안 걸린다.
#
#   `F0 9F` 은 U+1F000~1FFFF 로 시작하는 UTF-8 의 앞 두 바이트다. 한글은
#   EA~ED 로 시작하고 한자 확장은 `F0 A0` 이라 여기 안 걸린다 — 이모지 판만
#   정확히 집는다.
# ★ **조각은 한 바이트짜리다.** `b"\xf0\x9f"` 두 바이트를 찾았더니 하나도
#   안 걸렸다(막은 수가 1,593 그대로였다) — 어휘에 있는 것은 `b"\xf0"` 하나뿐이라
#   두 바이트 검사에 안 맞았다. 실측으로 좁혔다(2026-09-02).
#
# ★ **`F0` 한 바이트만 막으면 된다.** UTF-8 에서 `F0` 으로 시작하는 것은
#   U+10000 위쪽뿐이고 이모지가 거기 산다. 한글(EA~ED)·한자(E4~E9)·라틴은
#   안 걸린다. 앞 바이트가 막히면 뒤 조각만으로는 아무것도 못 만든다.
_EMOJI_LEAD = 0xF0


# ★ **조각은 글자로 돌아온다.** `detokenize` 가 바이트 대체 토큰을 `<0xF0>`
#   같은 **글**로 준다 — 그래서 위 바이트 검사가 하나도 못 잡았다(막은 수가
#   1,593 그대로였다). 실측으로 알았다(2026-09-02).
#
# ★ **`F0` 하나만 막으면 된다.** UTF-8 에서 `F0` 으로 시작하는 것은 U+10000
#   위쪽뿐이고, 이모지가 거기 산다. 한글(EA~ED)·한자(E4~E9)·라틴은 안 걸린다.
#   앞 바이트가 막히면 뒤 조각만으로는 아무것도 못 만든다.
_BYTE_PIECE = re.compile(r"^<0x([0-9A-Fa-f]{2})>$")


def _이모지인가(s: str, raw: bytes = b"") -> bool:
    if _EMOJI_LEAD in raw:                      # 조각(b"\xf0")도 통짜도 여기 걸린다
        return True
    m = _BYTE_PIECE.match(s.strip())            # `<0xF0>` 모양으로 줄 때도 대비한다
    if m and int(m.group(1), 16) == _EMOJI_LEAD:
        return True
    return any(lo <= ord(c) <= hi for c in s for lo, hi in _EMOJI_RANGES)


def 이모지토큰():
    """어휘에서 이모지가 든 토큰. 처음 한 번만 만든다."""
    global _금지토큰
    if _금지토큰 is None:
        막을것 = {}
        for tid in range(LLM.n_vocab()):
            try:
                raw = LLM.detokenize([tid])
                s = raw.decode("utf-8", "ignore")
            except Exception:  # noqa: BLE001
                continue
            if _이모지인가(s, raw):
                # -100 이면 실질적으로 절대 안 뽑힌다.
                막을것[tid] = -100.0
        _금지토큰 = 막을것
        print(f"이모지 토큰 {len(막을것):,} 개를 막는다", flush=True)
    return _금지토큰


class TooBig(RuntimeError):
    """이 자리에 안 들어간다. **죽는 대신 돌려보낸다.**"""


def _render(messages: list[dict], tools: list | None) -> str:
    """템플릿을 그대로 태워 프롬프트를 만든다. **토큰을 세려고 먼저 한 번 한다.**

    ★ 도구 목록은 템플릿에 맡긴다. 모델마다 그 자리 모양이 다르고, 손으로
      적으면 모델을 갈아 끼울 때마다 여기가 틀린다.
    """
    from llama_cpp.llama_chat_format import Jinja2ChatFormatter

    tmpl = LLM.metadata.get("tokenizer.chat_template") or ""
    fmt = Jinja2ChatFormatter(template=tmpl, eos_token="", bos_token="")
    kw = {"tools": tools} if tools else {}
    return fmt(messages=messages, **kw).prompt


지난입력 = 0
"""바로 앞 요청의 입력 토큰 수. **줄어들면 캐시를 비운다** — `generate` 안의
"줄어들면 비운다" 주석에 재현기와 함께 적어 뒀다."""


def generate(messages: list[dict], max_tokens: int, temperature: float,
             tools: list | None = None, tool_choice=None,
             repeat_penalty: float = 1.0, no_emoji: bool = False,
             grammar: str | None = None) -> dict:
    """한 번 만든다.

    ★ **`tool_choice` 로 하나를 못박으면 그 도구가 반드시 나온다.** 여기서
      llama-cpp-python 은 그 도구의 JSON 스키마로 문법(grammar)을 만들어
      표집을 조인다 — 모델이 협조하든 말든 인자 JSON 만 나온다. 부탁이 아니라
      제약이다.

      이 자리가 비어 있었다(2026-09-01). 루프는 지어낸 `[사진:...]` 을 걷고
      나서 `self_portrait` 를 강제하며 다시 물었는데, 어댑터가 `tool_choice`
      를 안 실었고 여기는 `"auto"` 로 못박혀 있었다. 그래서 강제는 한 번도
      일어나지 않았고, 화면에는 "self_portrait 를 강제한다" 만 찍혔다.
      오빠가 사진을 세 번 물었고 세 번 다 못 받았다.

    ★ **비우지 않는다.** transformers 때는 매 호출 끝에 `empty_cache()` 를 했다
      (조각이 쌓여 실제로 0.3 tok/s 까지 떨어졌다). llama.cpp 는 KV 를 미리
      잡아 두고 **접두사를 재사용한다** — 여기서 비우면 그 이득이 사라진다.
      실측: 접두사 76,000 토큰을 물고 있으면 새 입력 400 토큰 프리필이 0.26초,
      한 턴이 13.6초다. 안 물고 있으면 그 한 턴이 59초부터 시작한다.

    ★ 유나·예나의 프롬프트는 이미 접두사 캐시를 전제로 짜여 있다(안 변하는 것이
      앞, 매 턴 바뀌는 사실관계는 messages 끝). 클라우드에서 캐시가 입력의
      77% 를 먹는 것이 그 증거고, 여기서도 같이 먹는다.
    """
    with LOCK:
        prompt = _render(messages, tools)
        n_in = len(LLM.tokenize(prompt.encode("utf-8"), add_bos=True, special=True))
        # ★ **안 들어가면 만들기 전에 돌려보낸다.** `n_ctx` 는 미리 잡혀 있어서
        #   재는 게 아니라 세면 된다.
        if n_in + max_tokens > NCTX:
            raise TooBig(
                f"프롬프트가 {n_in:,} 토큰인데 답 {max_tokens:,} 를 더하면 "
                f"이 자리의 {NCTX:,} 를 넘는다")

        # ★ **줄어들면 비운다.** 위 "비우지 않는다" 의 예외이고, 재현기로
        #   잡은 자리다(2026-09-04):
        #
        #       그냥                                    도구 부름 5/5
        #       14,876토큰 한 번 태운 뒤 같은 접두사로     도구 부름 0/5
        #       그 상태에서 딴 접두사로 물으면             도구 부름 3/5  ← 살아난다
        #       긴 것을 딴 접두사로 태우면 원래 접두사는    도구 부름 4/5  ← 멀쩡
        #
        #   캐시가 긴 상태를 물고 있는데 **같은 접두사로 짧은 것**이 들어오면
        #   잘라내는 자리가 틀린다. 그 뒤로 도구 호출이 죽고 **스스로 안 낫는다.**
        #   실서비스 프롬프트가 15,000 토큰이라 이건 늘 걸리는 자리였다.
        #
        #   비우는 값은 짧은 쪽 프리필 한 번이다(154토큰이면 0.1초). 길어지는
        #   쪽 — 매 턴 조금씩 자라는 실제 대화 — 은 그대로 재사용한다.
        #
        # ★ **`LLM.n_tokens` 로 재면 안 된다.** 그 수에는 방금 **낸** 토큰까지
        #   들어 있어서 늘 `n_in` 보다 크다 — 그걸로 재니 매번 비웠고, 같은
        #   프롬프트를 다시 물어도 프리필이 12초씩 걸렸다. 우리가 **지난 입력
        #   길이**를 들고 그것과 견준다.
        global 지난입력
        if n_in < 지난입력:
            LLM.reset()
        지난입력 = n_in

        schemas = {t["function"]["name"]: t["function"].get("parameters")
                   for t in (tools or []) if t.get("function")}
        # ★ **문법(GBNF)을 받으면 그걸로 표집을 조인다.** llama.cpp 문서가 도구
        #   호출을 미덥게 만드는 표준 해법으로 드는 것이 이것이다 — 다만 그건
        #   `tool_choice` 로 **하나를 못박았을 때**의 이야기고, "무엇을 부를지"
        #   고르는 자리에는 안 걸린다.
        #
        #   그래서 여기를 연다. 고르는 쪽(`core.router`)이 "이름 하나만 내라" 는
        #   문법을 직접 주면, 4B 가 도구 호출 문법을 써낼 필요 없이 **이름만**
        #   고르면 된다. 잘못된 이름이 나올 수가 없다.
        문법 = None
        if grammar:
            from llama_cpp import LlamaGrammar
            문법 = LlamaGrammar.from_string(grammar, verbose=False)
        r = LLM.create_chat_completion(
            messages=messages,
            tools=tools or None,
            tool_choice=(tool_choice or "auto") if tools else None,
            **({"grammar": 문법} if 문법 is not None else {}),
            max_tokens=max_tokens,
            # 온도 0 이면 표집을 좁힌다. 정해진 모양을 내는 자리라 다양성이
            # 값이 아니다. 0 이 아니면 Gemma-4 권장값으로 간다.
            temperature=temperature,
            top_p=0.95 if temperature > 0 else 1.0,
            top_k=64 if temperature > 0 else 1,
            # ★ 권장값이 1.0 이다. 라이브러리 기본값 1.1 로 돌리면 말이 무너진다 —
            #   후보를 재다가 이걸로 한 모델을 불리하게 쟀다.
            #
            # ★ 그런데 1.0 은 **억제가 아예 없다**는 뜻이기도 하다. 예나가 자기
            #   지난 메시지를 글자까지 그대로 베끼는 일이 하루에 여러 번 났고
            #   (2026-09-01), 그게 이 값 때문인지를 재려면 값을 바꿔 가며
            #   물어봐야 한다. 그래서 **요청마다 받는다** — 기본값은 그대로
            #   1.0 이라 안 실어 보내면 지금까지와 똑같이 돈다.
            repeat_penalty=repeat_penalty,
            # ★ **부르는 쪽이 켠다.** 여기서 늘 막아 버리면 이 서버를 쓰는 다른
            #   자리(시험·다른 사람)까지 같이 막힌다. 위 주석 참고.
            logit_bias=이모지토큰() if no_emoji else None,
            stream=False,
        )
        choice = r["choices"][0]
        m = choice.get("message") or {}
        말, 부름 = 부른것(m.get("content") or "", schemas)
        # 라이브러리가 스스로 뽑아낸 것이 있으면 그것도 받는다
        if not 부름 and m.get("tool_calls"):
            부름 = m["tool_calls"]
        usage = r.get("usage") or {}
        끝났나 = choice.get("finish_reason") != "length"
        return {
            "text": 말,
            "calls": 부름,
            "in": int(usage.get("prompt_tokens") or n_in),
            "out": int(usage.get("completion_tokens") or 0),
            # 부른 것이 있으면 그게 멈춘 이유다 — 루프가 그걸 보고 돈다.
            "finish": "tool_calls" if 부름 else ("stop" if 끝났나 else "length"),
        }


def load_embedder(model_id: str):
    """임베더를 올린다. 이미 올라와 있으면 그대로.

    ★ **CPU 로 둔다**(2026-09-01). 카드에 두면 1,090MiB 를 물고, 그러면 채팅이
      자리를 필요로 할 때마다 내렸다 올려야 한다 — 다시 올리는 데 7초다.
      CPU 로 내리면 그 춤이 통째로 없어지고 그냥 상주한다.

      잃는 것은 회상 한 번에 0.05초다(실측):

        회상 질의 1줄   GPU 0.02초  ·  CPU 0.07초    ← 제일 잦은 자리
        긴 줄 1개       GPU 0.03초  ·  CPU 0.37초
        묶음 32줄       GPU 0.07초  ·  CPU 1.40초    ← 백필 때만

      백필(17,185줄)은 GPU 38초 · CPU 12분이다. 드물게 도는 자리라 그쪽에
      1GB 를 상시로 내주지 않는다. 급하면 `--embed-device cuda` 로 그때만 올린다.

    ★ CPU 에서는 fp32 다. fp16 은 CPU 에서 오히려 느리다.

    ★ **채팅 자물쇠를 같이 쓴다.** 카드에 올릴 때는 둘이 동시에 돌면 OOM 이었다.
      CPU 로 내린 뒤에도 자물쇠는 그대로 둔다 — 이 서버는 한 번에 하나만
      만드는 것이 값이고(사용자가 하나다), 자리를 옮겼다고 그게 바뀌지 않는다.
    """
    import torch
    from transformers import AutoModel, AutoTokenizer

    global EMB, EMB_TOK, EMB_NAME
    if EMB is not None and EMB_NAME == model_id:
        return EMB
    unload_embedder()
    dev = EMB_DEVICE
    dtype = torch.float16 if dev.startswith("cuda") else torch.float32
    print(f"  임베더 올린다 — {model_id} ({dev})", flush=True)
    EMB_TOK = AutoTokenizer.from_pretrained(model_id)
    EMB = AutoModel.from_pretrained(model_id, dtype=dtype).to(dev).eval()
    EMB_NAME = model_id
    print(f"  임베더 올렸다 ({dev}) — VRAM {vram()}", flush=True)
    return EMB


def unload_embedder() -> None:
    global EMB, EMB_TOK, EMB_NAME
    if EMB is None:
        return
    import torch

    EMB = EMB_TOK = None
    EMB_NAME = ""
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    print("  임베더 내렸다", flush=True)


def _reaper() -> None:
    """안 쓰는 임베더를 내리는 실. 1분마다 본다.

    ★ **어디에 있든 내리되, 임계가 다르다** — 카드 10분(`EMB_IDLE`),
      CPU 40분(`EMB_IDLE_CPU`).

      내리면 **사용자가 그 값을 문다.** 다시 올리는 데 13.5초고(실측), 그게
      회상 한 번의 앞에 붙는다 — 회상은 오빠가 앞에서 기다리는 자리다. 그래서
      한때 여기 "CPU 에 있으면 안 내린다" 고 적어 뒀었다.

      그 판단을 재서 뒤집었다(2026-09-03). 이 프로세스가 Private 커밋 7.4GB 를
      물고 있었고 그 중 임베더 몫이 fp32 로 2.3GB 였다. 15.6GB 짜리 노트북이
      93% 까지 찼을 때 그 2.3GB 는 "싼 쪽" 이 아니었다.

      **40분이 그 둘을 가른다.** 회상은 대화 중에 잦고, 대화 중에는 40분이
      비지 않는다. 40분이 비었다는 것은 그 대화가 끝났다는 뜻이라, 무는 13.5초는
      다음 대화의 첫 회상 한 번뿐이다.
    """
    while True:
        time.sleep(60)
        한도 = EMB_IDLE if EMB_DEVICE.startswith("cuda") else EMB_IDLE_CPU
        if EMB is not None and EMB_USED and time.time() - EMB_USED > 한도:
            with emb_lock():
                # 자물쇠를 잡는 사이에 누가 썼을 수 있다. 다시 본다.
                if EMB is not None and time.time() - EMB_USED > 한도:
                    unload_embedder()


def embed(texts: list[str], model_id: str) -> list[list[float]]:
    """bge-m3 의 dense 벡터 — **CLS 토큰을 정규화한 것**이다.

    질의와 문서에 접두사를 안 붙인다(bge-en 계열과 다르다).

    ★ **길이로 묶는다.** 기억은 중앙값이 100자 남짓인데 p99 가 2,800자다.
      고정 배치로 묶으면 짧은 줄 열둘이 긴 줄 하나에 맞춰 패딩되어 대부분이
      빈칸 계산이 된다.
    """
    import torch

    global EMB_USED
    # ★ 카드에 있으면 채팅과 같은 자물쇠, CPU 면 자기 것(`emb_lock` 주석).
    with emb_lock():
        model = load_embedder(model_id)
        EMB_USED = time.time()
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        out: list = [None] * len(texts)
        i = 0
        while i < len(order):
            긴것 = min(len(texts[order[i]]) // 2 + 8, EMB_MAXLEN)
            묶음 = max(1, min(EMB_BUDGET // max(긴것, 1), 32, len(order) - i))
            chunk = [texts[j] or " " for j in order[i : i + 묶음]]
            ins = EMB_TOK(chunk, padding=True, truncation=True,
                          max_length=EMB_MAXLEN, return_tensors="pt").to(model.device)
            with torch.no_grad():
                h = model(**ins).last_hidden_state[:, 0]
                h = torch.nn.functional.normalize(h, dim=-1).float().cpu()
            for k, j in enumerate(order[i : i + 묶음]):
                out[j] = h[k].tolist()
            del ins, h
            i += 묶음
        EMB_USED = time.time()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        return out


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        blob = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def do_GET(self):  # noqa: N802
        # 어댑터의 `available()` 은 포트만 두드리지만, 사람이 눌러 볼 자리도 둔다.
        if self.path.rstrip("/") in ("/health", "/v1/models"):
            # ★ **채움이 이 자리의 값이다.** 다음 턴이 2초냐 59초냐를 가르는 건
            #   창 크기가 아니라 **접두사가 얼마나 물려 있나** 다. 밖에서 볼 길이
            #   없어서 한동안 계산으로 짐작했다 — 계산은 틀린다.
            #
            #   KV 가 몇 MiB 인지는 **안 싣는다.** llama.cpp 가 조용히 잡는 값이라
            #   여기서 다시 계산하면 그건 실측이 아니라 또 다른 짐작이다.
            찬것 = getattr(LLM, "n_tokens", None) if LLM is not None else None
            self._send(200, {
                "status": "ok", "model": NAME, "n_ctx": NCTX,
                "채움": 찬것,
                "채움_%": (round(찬것 / NCTX * 100, 1) if (찬것 and NCTX) else 0),
                "VRAM": vram(),
                "임베더": (EMB_NAME + f" ({EMB_DEVICE})") if EMB is not None else None,
            })
        else:
            self._send(404, {"error": "없는 자리"})

    def do_POST(self):  # noqa: N802
        자리 = self.path.rstrip("/")
        if 자리 not in ("/v1/chat/completions", "/v1/embeddings"):
            self._send(404, {"error": "없는 자리"})
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            self._send(400, {"error": f"몸통을 못 읽었다: {e}"})
            return

        if 자리 == "/v1/embeddings":
            self._embed(body)
            return

        t0 = time.time()
        try:
            got = generate(
                body.get("messages") or [],
                int(body.get("max_tokens") or 512),
                float(body.get("temperature") or 0),
                body.get("tools"),
                body.get("tool_choice"),
                float(body.get("repeat_penalty") or 1.0),
                bool(body.get("no_emoji")),
                # ★ **있을 때만, 이름으로 넘긴다.** 위치로 붙이면 이 자리를
                #   통째로 흔내 내는 대역들이 전부 깨진다 — 안 쓰는 쪽은
                #   예전 모양 그대로 도는 것이 이 통로를 여는 값이다.
                **({"grammar": body["grammar"]} if body.get("grammar") else {}),
            )
        except TooBig as e:
            # ★ 413. 어댑터가 이걸 `LocalUnavailable` 로 올리고 부르는 쪽이
            #   클라우드로 되돌아간다 — 죽는 것과 되돌아가는 것은 다르다.
            print(f"  거절 — {e}", flush=True)
            self._send(413, {"error": str(e)})
            return
        except Exception as e:  # noqa: BLE001 — 무엇이든 어댑터에 알려준다
            self._send(500, {"error": f"{type(e).__name__}: {e}"})
            return

        걸린 = time.time() - t0
        부름 = got.get("calls") or []
        # ★ VRAM 을 같이 찍는다. 이 수가 조용히 자라는 것이 이 물건이 느려지는
        #   방식이라, 안 찍으면 느려진 뒤에야 안다.
        print(f"  {got['in']:>6}→{got['out']:<5} 토큰 · {걸린:5.1f}초 "
              f"· {got['out'] / max(걸린, 0.01):4.1f} tok/s · {got['finish']} · {vram()}"
              + (f" · {' '.join(c['function']['name'] for c in 부름)}" if 부름 else ""),
              flush=True)
        말 = {"role": "assistant", "content": got["text"]}
        if 부름:
            말["tool_calls"] = 부름
        self._send(200, {
            "id": "local", "object": "chat.completion", "model": NAME,
            "choices": [{"index": 0, "finish_reason": got["finish"], "message": 말}],
            "usage": {"prompt_tokens": got["in"], "completion_tokens": got["out"],
                      "total_tokens": got["in"] + got["out"]},
        })

    def _embed(self, body: dict) -> None:
        """OpenAI 호환 `/v1/embeddings`. 유나·예나의 회상이 여기로 온다."""
        입력 = body.get("input")
        if isinstance(입력, str):
            입력 = [입력]
        if not isinstance(입력, list) or not 입력:
            self._send(400, {"error": "input 이 비었다"})
            return
        model_id = body.get("model") or EMB_DEFAULT
        t0 = time.time()
        try:
            vecs = embed([str(x) for x in 입력], model_id)
        except Exception as e:  # noqa: BLE001 — 무엇이든 부른 쪽에 알려준다
            self._send(500, {"error": f"{type(e).__name__}: {e}"})
            return
        걸린 = time.time() - t0
        글자 = sum(len(str(x)) for x in 입력)
        print(f"  임베딩 {len(입력):>5}줄 · {글자:>7}자 · {걸린:5.1f}초 "
              f"· {len(vecs[0])}차원", flush=True)
        self._send(200, {
            "object": "list",
            "model": model_id,
            "data": [{"object": "embedding", "index": i, "embedding": v}
                     for i, v in enumerate(vecs)],
            # 이 자리에는 토큰 수를 세는 값이 없다. **0 을 보낸다** — 없는 것을
            # 지어내면 값 기록이 조용히 틀린다.
            "usage": {"prompt_tokens": 0, "total_tokens": 0},
        })

    def log_message(self, *a):
        pass  # 우리가 위에서 한 줄로 찍는다


# 유나·예나가 고른 모델이다(2026-09-01). 고른 근거는 princess 의 `coord/RUNTIME.md`.
#   어체가 무너지는 비율 1/48(2%) · 회상에 뭐가 있었는지 정확히 읽는다 · voice_reply 3/8
#   후보였던 Qwen3.5-4B 는 도구가 5/5 로 보였으나, 도구 설명이 요구하는 조건을
#   채워 다시 재니 2/8 이었고 어체 붕괴가 5/48(10%) 이었다.
DEFAULT_MODEL = os.path.expanduser(
    "~/.cache/huggingface/hub/models--unsloth--gemma-4-E4B-it-qat-GGUF/snapshots"
    "/8c5a9e4fd5482e2be20fe0bf013b4c262a8f4265/gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", default=DEFAULT_MODEL, help="gguf 경로")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    # ★ **평균으로 상한을 잡으면 안 된다.** 처음엔 `coord/COST.md` 의 "한 건에
    #   입력 6만~7.7만" 을 보고 76,800 으로 잡았는데, 그건 이틀치 **평균**이었고
    #   실제 분포는 절반이 그 위였다. 값 기록 311건으로 다시 세면(2026-09-01):
    #
    #     n_ctx      대화 덮는 비율   깨어남 덮는 비율   VRAM(Gemma-4)
    #      76,800        51%            85%            4,439 MiB (f16)
    #     102,400        86%            93%
    #     131,072        94%            94%            4,694 MiB (q8_0)
    #     153,600        95%            95%
    #     204,800        99%            96%
    #
    #   넘으면 `TooBig` 으로 거절하고 클라우드로 되돌아가므로 위험하진 않다.
    #   다만 절반이 되돌아가면 내린 값이 절반이다.
    #
    # ★ **131,072 에서 멈춘다. 그게 이 모델이 학습한 창이다**(`n_ctx_train`).
    #   더 크게 잡으면 llama.cpp 가 이렇게 말한다 —
    #     `n_ctx_seq (153600) > n_ctx_train (131072) -- possible training
    #      context overflow`
    #   숫자로는 153,600 이 1%p 더 덮지만 그 1%p 는 학습 창 밖이라 답이
    #   어떻게 나올지 모르는 자리다. **모르는 자리를 얻자고 아는 자리를
    #   흔들지 않는다.**
    #
    # ★ **위 표는 클라우드 시절 입력 크기로 고른 것이다** (2026-09-03에 다시 셈).
    #   그때는 한 건에 6만~7.7만 토큰이 왔다 — 기억을 통째로 실어 보내던 자리다.
    #   로컬은 작업 기억 예산이 따로 있어 그만큼 안 온다. 이 서버 로그
    #   **요청 1,203건**을 다시 세면:
    #
    #       최대 프롬프트   19,140 토큰
    #       상위 1%         18,338
    #       중간값          10,479
    #       24,576 초과          0 건
    #
    #   131,072 는 실제로 쓰는 것의 일곱 배였고, 안 쓰는 그 창이 전부 KV 로 나간다.
    #
    # ★ **그래서 32,768 이다** — 실측 최대의 1.7배. 넘으면 `TooBig` 으로 거절해
    #   클라우드로 되돌아가고 그게 값이 되므로 여유는 넉넉히 둔다.
    #
    # ★ **남은 자리로 KV 를 f16 으로 올린다.** 그 전에는 못 했다 — 같은 카드에
    #   그림 서버(~1.4GB)가 있었고 f16 이 131k 에서 +1,020 MiB 였다
    #   (`coord/RUNTIME.md` ②). 창을 4분의 1로 줄이면 그 값도 4분의 1이 되고,
    #   오빠가 그림 서버를 내리기로 했다(2026-09-03: "일단 그림은 내리자 말이
    #   더 중요해"). 그 문서에 재 둔 것이 **f16 이 디코드 25% 빠르고 더 정확**이다.
    p.add_argument("--n-ctx", type=int, default=32768)
    p.add_argument("--kv", default="f16", choices=["f16", "q8_0", "q4_0"])
    p.add_argument("--n-gpu-layers", type=int, default=-1, help="-1 = 전부 GPU")
    # ★ 임베더는 CPU 가 기본이다. 카드를 물면 채팅이 자리를 필요로 할 때마다
    #   내렸다 올려야 하고, 잃는 것은 회상 한 번에 0.05초뿐이다(load_embedder 참고).
    p.add_argument("--embed-device", default="cpu", help="cpu | cuda")
    args = p.parse_args()

    global EMB_DEVICE
    EMB_DEVICE = args.embed_device
    if not os.path.exists(args.model):
        print(f"  가중치가 없다: {args.model}", file=sys.stderr)
        return 1
    print(f"  {os.path.basename(args.model)} — 올리는 중", flush=True)
    load(args.model, args.n_ctx, args.n_gpu_layers, args.kv)
    print(f"  http://{args.host}:{args.port}/v1/chat/completions 에서 듣는다", flush=True)
    print(f"  http://{args.host}:{args.port}/v1/embeddings 도 같은 자리다 "
          f"({EMB_DEFAULT} · {EMB_DEVICE} — 부를 때 올린다)", flush=True)
    threading.Thread(target=_reaper, daemon=True).start()
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
