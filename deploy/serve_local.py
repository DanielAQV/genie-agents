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

# 임베더는 따로 든다. **부를 때 올리고 안 쓰면 내린다** — 6GB 에서 채팅과
# 임베딩이 같이 앉아 있으면 자리가 없다. Gemma-4 가 76K 를 물면 4,439MiB 를
# 쓰고 남는 것이 1.7GB 인데 bge-m3 fp16 은 2.3GB 다. 그래서 이 규칙은
# llama.cpp 로 바꾼 뒤에도 그대로 필요하다.
EMB = None
EMB_TOK = None
EMB_NAME = ""
EMB_USED = 0.0
EMB_IDLE = 600.0   # 이만큼 안 쓰면 내린다
EMB_MAXLEN = 2048  # 기억 한 줄 p99 가 2,800자 ≈ 1,600토큰. 여기까지 덮는다
EMB_BUDGET = 6144  # 한 묶음의 토큰 상한
EMB_DEFAULT = "BAAI/bge-m3"  # 다국어. 유나·예나 기억이 한국어라 여기가 갈린다

LOCK = threading.Lock()
"""한 번에 하나만 만든다.

★ `ThreadingHTTPServer` 는 요청마다 실을 하나 낸다. 6GB 짜리 카드에서 생성이
  둘 겹치면 KV 캐시가 두 벌 잡히고 그대로 OOM 이다. 상주 서버의 값은 가중치를
  한 번만 올리는 것이지 동시에 여럿을 받는 것이 아니다.

★ 사용자가 짚었다(2026-09-01) — "실제 사용자는 나 하나뿐이라는 거 잊지마."
  맞다. **동시성 예산은 잡을 필요가 없다.**"""


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


def generate(messages: list[dict], max_tokens: int, temperature: float,
             tools: list | None = None) -> dict:
    """한 번 만든다.

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

        schemas = {t["function"]["name"]: t["function"].get("parameters")
                   for t in (tools or []) if t.get("function")}
        r = LLM.create_chat_completion(
            messages=messages,
            tools=tools or None,
            tool_choice="auto" if tools else None,
            max_tokens=max_tokens,
            # 온도 0 이면 표집을 좁힌다. 정해진 모양을 내는 자리라 다양성이
            # 값이 아니다. 0 이 아니면 Gemma-4 권장값으로 간다.
            temperature=temperature,
            top_p=0.95 if temperature > 0 else 1.0,
            top_k=64 if temperature > 0 else 1,
            # ★ 권장값이 1.0 이다. 라이브러리 기본값 1.1 로 돌리면 말이 무너진다 —
            #   후보를 재다가 이걸로 한 모델을 불리하게 쟀다.
            repeat_penalty=1.0,
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

    ★ **채팅 자물쇠를 같이 쓴다.** 6GB 에서 둘이 동시에 돌면 OOM 이고, 그 OOM 은
      답하는 쪽을 죽인다. 부르는 쪽이 이미 `LOCK` 을 쥐고 들어온다.
    """
    import torch  # noqa: F401  (여기서 CUDA 가 잡혀 있어야 아래 .to("cuda") 가 산다)
    from transformers import AutoModel, AutoTokenizer

    global EMB, EMB_TOK, EMB_NAME
    if EMB is not None and EMB_NAME == model_id:
        return EMB
    unload_embedder()
    print(f"  임베더 올린다 — {model_id}", flush=True)
    EMB_TOK = AutoTokenizer.from_pretrained(model_id)
    EMB = AutoModel.from_pretrained(model_id, dtype=torch.float16).to("cuda").eval()
    EMB_NAME = model_id
    print(f"  임베더 올렸다 — VRAM {vram()}", flush=True)
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
    """안 쓰는 임베더를 내리는 실. 1분마다 본다."""
    while True:
        time.sleep(60)
        if EMB is not None and EMB_USED and time.time() - EMB_USED > EMB_IDLE:
            with LOCK:
                if EMB is not None and time.time() - EMB_USED > EMB_IDLE:
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
    with LOCK:
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
            self._send(200, {"status": "ok", "model": NAME, "n_ctx": NCTX})
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
    # ★ q8_0 이 f16 보다 낫다 — 같은 창에서 KV 가 절반이고, 남는 자리가
    #   임베더 몫이 된다.
    p.add_argument("--n-ctx", type=int, default=131072)
    p.add_argument("--kv", default="q8_0", choices=["f16", "q8_0", "q4_0"])
    p.add_argument("--n-gpu-layers", type=int, default=-1, help="-1 = 전부 GPU")
    args = p.parse_args()

    if not os.path.exists(args.model):
        print(f"  가중치가 없다: {args.model}", file=sys.stderr)
        return 1
    print(f"  {os.path.basename(args.model)} — 올리는 중", flush=True)
    load(args.model, args.n_ctx, args.n_gpu_layers, args.kv)
    print(f"  http://{args.host}:{args.port}/v1/chat/completions 에서 듣는다", flush=True)
    print(f"  http://{args.host}:{args.port}/v1/embeddings 도 같은 자리다 "
          f"({EMB_DEFAULT} — 부를 때 올린다)", flush=True)
    threading.Thread(target=_reaper, daemon=True).start()
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
