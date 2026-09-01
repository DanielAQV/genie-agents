"""로컬 모델을 OpenAI 호환 한 구멍으로 낸다.

    python deploy/serve_local.py --model Qwen/Qwen3-4B-Instruct-2507

★ **어댑터를 안 늘리려고 이걸 둔다.** `adapters/local.py` 는 이미 OpenAI 호환
  `/v1/chat/completions` 하나에 붙는다 — llama.cpp 서버 · vLLM 이 내는 그
  모양이다. 여기서 transformers 를 쓰려고 두 번째 어댑터를 만들면, 골격이
  *"루프는 한 모양만 본다"* 고 약속한 자리가 모델 런타임마다 갈리기 시작한다.
  갈리는 것을 이 파일 한 장으로 막는다 — **나중에 llama.cpp 로 갈아타도
  골격은 한 줄도 안 바뀐다.**

★ **상주한다.** 이 골격의 다른 것들은 단발 실행인데 이건 아니다 — 4B 가중치를
  깨어날 때마다 올리면 매번 십수 초를 버린다. 대신 이 프로세스가 죽어도
  `wake` 는 그냥 "로컬 모델이 안 떠 있다" 를 말하고 넘어간다(`runner.check`).

━━ 왜 transformers 인가 ━━

이 기계는 **Smart App Control** 이 서명 없는 실행 파일을 막는다(2026-08-31 확인).
llama.cpp 프리빌트 바이너리가 전부 `0xC0000142` 로 죽는다. pip 로 깐 네이티브
확장은 통과하므로 파이썬 쪽으로 왔다. 그리고 이 스택(torch+bitsandbytes)은
**QLoRA 에도 그대로 필요하다** — 버리는 일이 아니다.

━━ 의존성 ━━

    pip install torch --index-url https://download.pytorch.org/whl/cu126
    pip install transformers bitsandbytes accelerate

★ 이건 `pyproject.toml` 에 안 넣는다. 골격은 모델 SDK 를 안 짊어진다 —
  이 파일은 골격이 아니라 **호스트 쪽 도구**다(`deploy/` 에 있는 이유).
"""

from __future__ import annotations

import argparse
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = None
TOK = None
NAME = ""

# 임베더는 따로 든다. **부를 때 올리고 안 쓰면 내린다** — 6GB 에서 채팅과
# 임베딩이 같이 앉아 있으면 4.8GB 가 늘 차고, 채팅 쪽 KV 캐시 자리가 준다.
EMB = None
EMB_TOK = None
EMB_NAME = ""
EMB_USED = 0.0
EMB_IDLE = 600.0   # 이만큼 안 쓰면 내린다
EMB_MAXLEN = 2048  # 기억 한 줄 p99 가 2,800자 ≈ 1,600토큰. 여기까지 덮는다
EMB_BUDGET = 6144  # 한 묶음의 토큰 상한
EMB_DEFAULT = "BAAI/bge-m3"  # 다국어. 유나·예나 기억이 한국어라 여기가 갈린다

# 남은 VRAM 중 KV 캐시에 내줄 몫. 나머지는 활성값과 조각 여유다.
KV_SHARE = 0.6
# 안전바닥. VRAM 을 못 읽는 기계(CPU 전용)에서 이 값을 쓴다.
FLOOR_TOKENS = 4096
LOCK = threading.Lock()
"""한 번에 하나만 만든다.

★ `ThreadingHTTPServer` 는 요청마다 실을 하나 낸다. 6GB 짜리 카드에서 생성이
  둘 겹치면 KV 캐시가 두 벌 잡히고 그대로 OOM 이다. 상주 서버의 값은 가중치를
  한 번만 올리는 것이지 동시에 여럿을 받는 것이 아니다."""


def load(model_id: str, bits: int, device: str):
    """가중치를 한 번 올린다. **4bit 가 기본이다** — 6GB 짜리 카드에서
    4B 를 bf16 으로 올리면 안 들어간다(8GB 넘는다)."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    global MODEL, TOK, NAME
    NAME = model_id
    TOK = AutoTokenizer.from_pretrained(model_id)

    kw = {"dtype": torch.bfloat16, "device_map": device}
    if bits == 4:
        from transformers import BitsAndBytesConfig

        kw["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            # ★ 이중 양자화. 6GB 에서 이 한 줄이 여유를 만든다.
            bnb_4bit_use_double_quant=True,
        )
    MODEL = AutoModelForCausalLM.from_pretrained(model_id, **kw)
    MODEL.eval()
    if torch.cuda.is_available():
        쓴것 = torch.cuda.memory_allocated() / 1024**3
        전체 = torch.cuda.get_device_properties(0).total_memory / 1024**3
        print(f"  올렸다 — VRAM {쓴것:.2f}GB / {전체:.1f}GB", flush=True)
    return MODEL


# Qwen3 가 도구를 부를 때 내는 모양. 특수 토큰이 아니라 그냥 글자라
# `skip_special_tokens=True` 로 풀어도 그대로 남는다.
CALL = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)


def 부른것(text: str) -> tuple[str, list[dict]]:
    """(도구 부분을 뺀 글, OpenAI 모양의 tool_calls).

    ★ **못 읽는 것은 글로 남긴다.** 4B 는 JSON 을 어긋나게 낼 때가 있는데,
      여기서 버리면 그 판이 아무 말도 없이 끝난다. 어댑터가 빈 인자로
      부르고 도구 쪽에서 받아 내게 두는 편이 낫다.
    """
    calls = []
    for i, m in enumerate(CALL.finditer(text)):
        try:
            got = json.loads(m.group(1))
        except ValueError:
            continue
        calls.append(
            {
                "id": f"call_{i}",
                "type": "function",
                "function": {
                    "name": got.get("name") or "",
                    "arguments": json.dumps(got.get("arguments") or {}, ensure_ascii=False),
                },
            }
        )
    return CALL.sub("", text).strip(), calls


class TooBig(RuntimeError):
    """이 카드에 안 들어간다. **죽는 대신 돌려보낸다.**"""


def kv_bytes_per_token() -> int:
    """토큰 하나가 KV 캐시에서 먹는 바이트. 모델 설정에서 그대로 읽는다."""
    c = MODEL.config
    층 = c.num_hidden_layers
    헤드 = getattr(c, "num_key_value_heads", None) or c.num_attention_heads
    폭 = getattr(c, "head_dim", None) or (c.hidden_size // c.num_attention_heads)
    return 2 * 층 * 헤드 * 폭 * 2  # K·V × fp16


def budget_tokens(more: int) -> int:
    """지금 남은 VRAM 으로 몇 토큰까지 되나.

    ★ **고정값을 안 쓴다.** 임베더가 올라와 있으면 자리가 절반으로 준다.
      부를 때 재면 카드를 바꾸든 모델을 바꾸든 저절로 따라간다.
    """
    import torch

    if not torch.cuda.is_available():
        return FLOOR_TOKENS
    전체 = torch.cuda.get_device_properties(0).total_memory
    쓴것 = torch.cuda.memory_reserved(0)
    남은 = max(전체 - 쓴것, 0) * KV_SHARE
    return max(int(남은 / kv_bytes_per_token()) - more, FLOOR_TOKENS)


def generate(messages: list[dict], max_tokens: int, temperature: float,
             tools: list | None = None) -> dict:
    """한 번 만든다. **끝나면 캐시를 비운다.**

    ★ 안 비우면 KV 캐시 조각이 쌓인다. 실측(2026-08-31): 20호출을 돌리고 나니
      가중치가 2.49GB 인데 VRAM 이 **5,933 / 6,144 MiB** 였고, 속도가 6.6 →
      0.3 tok/s 로 떨어지다 한 호출이 **833초**를 썼다. 카드가 작을수록
      "언젠가 알아서 정리되겠지" 가 안 통한다.
    """
    import torch

    with LOCK:
        # ★ **도구 목록은 템플릿에 맡긴다.** 모델마다 그 자리 모양이 다르고,
        #   손으로 적으면 모델을 갈아 끼울 때마다 여기가 틀린다.
        kw = {"tools": tools} if tools else {}
        text = TOK.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True, **kw
        )
        ins = TOK(text, return_tensors="pt").to(MODEL.device)
        n_in = ins.input_ids.shape[-1]
        # ★ **안 들어가면 만들기 전에 돌려보낸다.** 넘치면 bitsandbytes 가
        #   C 쪽에서 중단해 프로세스가 통째로 죽고, 그러면 임베딩까지 멈춘다.
        여유 = budget_tokens(max_tokens)
        if n_in > 여유:
            del ins
            raise TooBig(
                f"프롬프트가 {n_in:,} 토큰인데 지금 이 카드에 들어가는 것은 "
                f"약 {여유:,} 토큰이다(KV 캐시 토큰당 "
                f"{kv_bytes_per_token() // 1024}KB)"
            )
        try:
            with torch.no_grad():
                out = MODEL.generate(
                    **ins,
                    max_new_tokens=max_tokens,
                    # ★ 온도 0 이면 표집을 끈다. 정해진 모양의 JSON 을 내는
                    #   자리라 다양성이 값이 아니다.
                    do_sample=temperature > 0,
                    temperature=temperature or None,
                    top_p=0.9 if temperature > 0 else None,
                    pad_token_id=TOK.eos_token_id,
                )
            새것 = out[0][n_in:].clone()
            끝났나 = 새것.shape[-1] < max_tokens
            말, 부름 = 부른것(TOK.decode(새것, skip_special_tokens=True))
            got = {
                "text": 말,
                "calls": 부름,
                "in": n_in,
                "out": int(새것.shape[-1]),
                # 부른 것이 있으면 그게 멈춘 이유다 — 루프가 그걸 보고 돈다.
                "finish": "tool_calls" if 부름 else ("stop" if 끝났나 else "length"),
            }
        finally:
            # `locals().pop(...)` 은 아무것도 안 지운다 — CPython 에서
            # `locals()` 는 사본이다. 이름을 직접 지워야 참조가 풀린다.
            out = ins = 새것 = None
            del out, ins, 새것
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        return got


def load_embedder(model_id: str):
    """임베더를 올린다. 이미 올라와 있으면 그대로.

    ★ **채팅 자물쇠를 같이 쓴다.** 6GB 에서 둘이 동시에 돌면 OOM 이고, 그 OOM 은
      답하는 쪽을 죽인다. 부르는 쪽이 이미 `LOCK` 을 쥐고 들어온다.
    """
    import torch
    from transformers import AutoModel, AutoTokenizer

    global EMB, EMB_TOK, EMB_NAME
    if EMB is not None and EMB_NAME == model_id:
        return EMB
    unload_embedder()
    print(f"  임베더 올린다 — {model_id}", flush=True)
    EMB_TOK = AutoTokenizer.from_pretrained(model_id)
    EMB = AutoModel.from_pretrained(model_id, dtype=torch.float16).to("cuda").eval()
    EMB_NAME = model_id
    if torch.cuda.is_available():
        print(f"  임베더 올렸다 — VRAM {torch.cuda.memory_allocated() / 1024**3:.2f}GB",
              flush=True)
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
            self._send(200, {"status": "ok", "model": NAME})
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
        쓴것 = ""
        try:
            import torch

            if torch.cuda.is_available():
                쓴것 = f" · VRAM {torch.cuda.memory_reserved() / 1024**3:.2f}GB"
        except Exception:  # noqa: BLE001
            pass
        # ★ VRAM 을 같이 찍는다. 이 수가 조용히 자라는 것이 이 물건이
        #   느려지는 방식이라, 안 찍으면 느려진 뒤에야 안다.
        부름 = got.get("calls") or []
        print(f"  {got['in']:>5}→{got['out']:<5} 토큰 · {걸린:5.1f}초 "
              f"· {got['out'] / max(걸린, 0.01):4.1f} tok/s · {got['finish']}{쓴것}"
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


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", default="Qwen/Qwen3-4B-Instruct-2507")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--bits", type=int, default=4, choices=[4, 16],
                   help="4 = bitsandbytes NF4 (6GB 짜리 카드의 기본값)")
    p.add_argument("--device", default="cuda:0")
    args = p.parse_args()

    print(f"  {args.model} · {args.bits}bit · {args.device} — 올리는 중", flush=True)
    load(args.model, args.bits, args.device)
    print(f"  http://{args.host}:{args.port}/v1/chat/completions 에서 듣는다", flush=True)
    print(f"  http://{args.host}:{args.port}/v1/embeddings 도 같은 자리다 "
          f"({EMB_DEFAULT} — 부를 때 올린다)", flush=True)
    threading.Thread(target=_reaper, daemon=True).start()
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
