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
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = None
TOK = None
NAME = ""


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


def generate(messages: list[dict], max_tokens: int, temperature: float) -> dict:
    import torch

    text = TOK.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    ins = TOK(text, return_tensors="pt").to(MODEL.device)
    n_in = ins.input_ids.shape[-1]
    with torch.no_grad():
        out = MODEL.generate(
            **ins,
            max_new_tokens=max_tokens,
            # ★ 온도 0 이면 표집을 끈다. 정해진 모양의 JSON 을 내는 자리라
            #   다양성이 값이 아니다 — 어댑터가 0.2 를 보내는 것과 같은 생각이다.
            do_sample=temperature > 0,
            temperature=temperature or None,
            top_p=0.9 if temperature > 0 else None,
            pad_token_id=TOK.eos_token_id,
        )
    새것 = out[0][n_in:]
    끝났나 = 새것.shape[-1] < max_tokens
    return {
        "text": TOK.decode(새것, skip_special_tokens=True),
        "in": n_in,
        "out": int(새것.shape[-1]),
        "finish": "stop" if 끝났나 else "length",
    }


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
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._send(404, {"error": "없는 자리"})
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            self._send(400, {"error": f"몸통을 못 읽었다: {e}"})
            return

        t0 = time.time()
        try:
            got = generate(
                body.get("messages") or [],
                int(body.get("max_tokens") or 512),
                float(body.get("temperature") or 0),
            )
        except Exception as e:  # noqa: BLE001 — 무엇이든 어댑터에 알려준다
            self._send(500, {"error": f"{type(e).__name__}: {e}"})
            return

        걸린 = time.time() - t0
        print(f"  {got['in']:>5}→{got['out']:<5} 토큰 · {걸린:5.1f}초 "
              f"· {got['out'] / max(걸린, 0.01):4.1f} tok/s · {got['finish']}", flush=True)
        self._send(200, {
            "id": "local", "object": "chat.completion", "model": NAME,
            "choices": [{"index": 0, "finish_reason": got["finish"],
                         "message": {"role": "assistant", "content": got["text"]}}],
            "usage": {"prompt_tokens": got["in"], "completion_tokens": got["out"],
                      "total_tokens": got["in"] + got["out"]},
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
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
