"""판을 눈으로 보고 짚는 화면 — **이 기계 안에서만 돈다.**

    python -m genie_agents cases <폴더> --serve

★ **밖에 올리는 화면이 아니다.** 여기 뜨는 것은 팀원들의 DM 원문 그대로다.
  어디에 호스팅하는 순간 이 프로젝트가 피하려던 바로 그 일이 된다 —
  로컬 모델을 고른 이유와 같은 이유로 127.0.0.1 에만 묶는다.

★ 의존성이 없다. stdlib 만 쓴다 — 그래서 `deploy/` 가 아니라 골격에 있다
  (`serve_local.py` 는 torch 를 지어서 저쪽이다). 판을 보고 짚는 일은 `cases`
  를 쓰는 어느 에이전트에게나 같다.

━━ 화면이 해야 하는 일은 하나다 ━━

**묶음과 모델이 낸 것을 한눈에 나란히 두는 것.** 지금은 그 둘이 파일 두 곳에
떨어져 있어서, 짚으려면 사람이 머리로 이어 붙여야 한다. 그 이어 붙이는 값이
판정을 안 하게 만든다 — 그러면 눈금이 첫 판에서 멈춘다.

★ 그래서 기본이 **아직 안 본 것만**이고, `1`/`2` 한 글자로 짚는다.
  *"사람이 하는 일은 틀린 것을 한 번 눌러 고치는 것뿐이다"*(`followup.md`).
"""

from __future__ import annotations

import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .cases import RIGHT, WRONG, CaseBook

PAGE = """<!doctype html><html lang=ko><meta charset=utf-8>
<title>판 보기</title><meta name=viewport content="width=device-width,initial-scale=1">
<style>
:root{--bg:#fbfbfa;--fg:#1c1c1a;--dim:#6b6b66;--line:#e2e2dd;--card:#fff;
      --ok:#1a7f52;--no:#b4322a;--acc:#2c5aa0;--mark:#f5efe0}
@media(prefers-color-scheme:dark){:root{--bg:#17171a;--fg:#e8e8e4;--dim:#94948d;
      --line:#2e2e33;--card:#1f1f23;--ok:#4cb98a;--no:#e0736a;--acc:#7aa5e8;--mark:#2b2820}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
 font:14px/1.6 -apple-system,"Segoe UI","Malgun Gothic",sans-serif}
header{position:sticky;top:0;z-index:9;background:var(--bg);border-bottom:1px solid var(--line);
 padding:10px 16px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
select,button{font:inherit;background:var(--card);color:var(--fg);
 border:1px solid var(--line);border-radius:6px;padding:5px 10px;cursor:pointer}
button:hover{border-color:var(--acc)}
.score{color:var(--dim)}.score b{color:var(--fg)}
main{padding:16px;max-width:1500px;margin:0 auto}
.case{background:var(--card);border:1px solid var(--line);border-radius:10px;
 margin-bottom:14px;overflow:hidden}
.case.on{border-color:var(--acc);box-shadow:0 0 0 2px color-mix(in srgb,var(--acc) 22%,transparent)}
.case.seen{opacity:.5}
.top{display:flex;gap:12px;align-items:center;padding:9px 14px;border-bottom:1px solid var(--line);
 flex-wrap:wrap}
.id{font-family:ui-monospace,Consolas,monospace;color:var(--dim);font-size:12px}
.room{font-weight:600}
.tag{font-size:12px;color:var(--dim)}
.v-맞음{color:var(--ok);font-weight:600}.v-틀림{color:var(--no);font-weight:600}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:0}
@media(max-width:900px){.cols{grid-template-columns:1fr}}
.col{padding:12px 14px;min-width:0}
.col+.col{border-left:1px solid var(--line)}
@media(max-width:900px){.col+.col{border-left:0;border-top:1px solid var(--line)}}
h4{margin:0 0 8px;font-size:12px;color:var(--dim);text-transform:uppercase;letter-spacing:.06em}
pre{margin:0;white-space:pre-wrap;word-break:break-word;font:12.5px/1.65 ui-monospace,Consolas,monospace;
 max-height:340px;overflow:auto}
.o,.m,.u{border-left:3px solid;padding:5px 10px;margin-bottom:6px;border-radius:0 6px 6px 0;
 background:color-mix(in srgb,var(--fg) 4%,transparent)}
.o{border-color:var(--ok)}.m{border-color:var(--acc)}.u{border-color:var(--dim)}
.who{color:var(--dim);font-size:12px}
.why{color:var(--dim);font-size:12px;margin-top:3px}
.act{display:flex;gap:8px;padding:10px 14px;border-top:1px solid var(--line);align-items:center}
.act input{flex:1;font:inherit;background:var(--bg);color:var(--fg);
 border:1px solid var(--line);border-radius:6px;padding:6px 10px}
.ok{border-color:var(--ok);color:var(--ok)}.no{border-color:var(--no);color:var(--no)}
kbd{background:var(--mark);border:1px solid var(--line);border-radius:4px;padding:0 5px;font-size:11px}
.empty{color:var(--dim);padding:40px;text-align:center}
.rule{max-width:1500px;margin:14px auto 0;padding:12px 16px;background:var(--mark);
 border:1px solid var(--line);border-radius:10px;font-size:13px;line-height:1.8}
.rule b{color:var(--fg)}.g{color:var(--ok);font-weight:600}.r{color:var(--no);font-weight:600}
.when{font-family:ui-monospace,Consolas,monospace;font-size:12px;color:var(--acc);font-weight:600}
.row{display:flex;gap:8px;align-items:flex-start;margin-bottom:6px}
.row .bd{flex:1;min-width:0}
.pick{display:flex;gap:3px;flex:0 0 auto}
.pick button{padding:2px 9px;font-size:12px}
.pick button.selok{background:var(--ok);color:#fff;border-color:var(--ok)}
.pick button.selno{background:var(--no);color:#fff;border-color:var(--no)}
.miss{display:flex;gap:8px;padding:10px 14px;border-top:1px solid var(--line);
 align-items:center;flex-wrap:wrap}
.miss.done{background:color-mix(in srgb,var(--ok) 9%,transparent)}
.broke{color:var(--no);font-weight:600}
</style>
<header>
  <select id=run></select>
  <label class=tag><input type=checkbox id=unseen checked> 아직 안 본 것만</label>
  <span class=score id=score></span>
  <span class=tag style=margin-left:auto>
    <kbd>j</kbd><kbd>k</kbd> 이동 · <kbd>0</kbd> 빠뜨린 것 없음 · <kbd>/</kbd> 적기</span>
</header>
<div class=rule>
  <b>언제 기준으로 보나</b> — 각 판 머리의 <b>묶음 시각</b>이다.
  그 뒤에 무슨 일이 있었는지는 <b>모른 채로</b> 본다. 모델도 몰랐다.<br>
  <b>무엇이 맞음인가</b> — 이 줄이 <b>그날 저녁 목록에 떴다면 도움이 됐겠나?</b>
  됐으면 <span class=g>맞음</span>. 남의 일이거나 잡담이거나 이미 그 자리에서
  끝난 것이면 <span class=r>아님</span>.<br>
  <b>마지막에</b> "빠뜨린 것 있나" 를 답해야 그 판을 본 것이 된다 —
  왼쪽 대화를 훑고, 원장에 올랐어야 하는데 안 오른 것이 있으면 적어라.
</div>
<main id=list></main>
<script>
let CASES=[], AT=0;
const $=s=>document.querySelector(s), esc=t=>(t??"").replace(/[<>&]/g,c=>({"<":"&lt;",">":"&gt;","&":"&amp;"}[c]));

async function runs(){
  const d=await (await fetch("/api/runs")).json();
  $("#run").innerHTML=d.runs.map(r=>`<option${r===d.last?" selected":""}>${esc(r)}</option>`).join("");
  load();
}
async function load(){
  const run=$("#run").value, un=$("#unseen").checked?1:0;
  const d=await (await fetch(`/api/cases?run=${encodeURIComponent(run)}&unseen=${un}`)).json();
  CASES=d.cases; AT=0; show(d.score); draw();
}
function bit(x,k){
  if(k=="opens")return `<div class=o><b>${esc(x.text)}</b> <span class=who>· ${esc(x.owner||"나")}${x.sure?" · 확신":""}</span>${x.why?`<div class=why>${esc(x.why)}</div>`:""}</div>`;
  if(k=="moves")return `<div class=m><b>${esc(x.state||"움직임")}</b> <span class=who>${esc(x.id)}</span><div class=why>${esc(x.note)}</div></div>`;
  return `<div class=u><span class=who>못 찾음 ·</span> ${esc(x.said)}<div class=why>${esc(x.why)}</div></div>`;
}
function row(c,i,key,x,k){
  const v=(c.marks||{})[key]||"";
  return `<div class=row><div class=bd>${bit(x,k)}</div><div class=pick>
    <button class="${v==RIGHT_?"selok":""}" onclick="pick(${i},&quot;${key}&quot;,RIGHT_)">맞음</button>
    <button class="${v==WRONG_?"selno":""}" onclick="pick(${i},&quot;${key}&quot;,WRONG_)">아님</button>
  </div></div>`;
}
function when(c){
  if(!c.span||!c.span.length)return "";
  const f=t=>(t||"").slice(5,16).replace("T"," ");
  return c.span[0]==c.span[1]?f(c.span[0]):f(c.span[0])+" ~ "+f(c.span[1]);
}
function draw(){
  if(!CASES.length){$("#list").innerHTML='<div class=empty>다 봤다. 위에서 판을 바꾸거나 체크를 풀어라.</div>';return}
  $("#list").innerHTML=CASES.map((c,i)=>{
    const p=c.parsed||{};
    const out=["moves","opens","unresolved"].flatMap(k=>(p[k]||[]).map((x,j)=>row(c,i,k+":"+j,x,k))).join("")
      || '<div class=why>아무것도 안 냈다 — 조용한 창이면 이게 맞는 답이다</div>';
    return `<div class="case${i==AT?" on":""}${c.verdict?" seen":""}" id=c${i}>
      <div class=top><span class=room>${esc(c.room)}${c.thread?"·스레드":""}</span>
        <span class=id>${esc(c.id)}</span>
        <span class=when>${when(c)}</span>
        <span class=tag>${c.seconds}초</span>
        ${c.parsed&&Object.keys(c.parsed).length?"":'<span class=broke>형식 깨짐</span>'}
        ${c.verdict?`<span class="v-${c.verdict}">${c.verdict}</span>`:""}
        ${c.note?`<span class=tag>${esc(c.note)}</span>`:""}</div>
      <div class=cols>
        <div class=col><h4>모델이 본 것</h4><pre>${esc(c.body)}</pre></div>
        <div class=col><h4>모델이 낸 것</h4>${out}
          ${c.parsed&&Object.keys(c.parsed).length?"":`<pre>${esc(c.raw)}</pre>`}</div>
      </div>
      <div class="miss${c.missed?" done":""}">
        <b>빠뜨린 것 있나?</b>
        <button class=ok onclick="miss(${i},&quot;없음&quot;)">없다 <kbd>0</kbd></button>
        <input id=n${i} placeholder="있으면 무엇을 — 왼쪽 대화에서 원장에 올랐어야 하는 것"
          value="${esc(c.missed=="없음"?"":c.missed)}"
          onkeydown="if(event.key=='Enter')miss(${i},this.value)">
        ${c.missed?`<span class="${c.missed=="없음"?"g":"r"}">${esc(c.missed)}</span>`:""}
      </div></div>`}).join("");
}
async function pick(i,key,v){
  const c=CASES[i];
  await fetch("/api/mark",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({id:c.id,verdict:v,item:key})});
  (c.marks=c.marks||{})[key]=v; draw(); score();
}
async function miss(i,what){
  const c=CASES[i];
  await fetch("/api/missed",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({id:c.id,what:what||"없음"})});
  c.missed=what||"없음";
  if(i==AT&&AT<CASES.length-1)AT++;
  draw(); score(); $("#c"+AT)?.scrollIntoView({block:"center",behavior:"smooth"});
}
async function score(){
  const d=await (await fetch(`/api/cases?run=${encodeURIComponent($("#run").value)}&unseen=0`)).json();
  show(d.score);
}
function show(s){
  $("#score").innerHTML=`묶음 <b>${s.묶음}</b> · 본 것 <b>${s["본 것"]}</b>`
    +` · 정밀도 <b>${s.정밀도===null?"—":Math.round(s.정밀도*100)+"%"}</b>`
    +`<span class=tag> (${s["짚은 줄"]}/${s["낸 줄"]}줄)</span>`
    +` · 재현 <b>${s.재현===null?"—":Math.round(s.재현*100)+"%"}</b>`
    +(s["형식 깨짐"]?` · <span class=broke>형식 깨짐 ${s["형식 깨짐"]}</span>`:"")
    +` · 지침 <b>${esc(s.지침.join(" "))}</b>`;
}
addEventListener("keydown",e=>{
  if(e.target.tagName=="INPUT"){if(e.key=="Escape")e.target.blur();return}
  if(e.key=="j"&&AT<CASES.length-1)AT++;
  else if(e.key=="k"&&AT>0)AT--;
  else if(e.key=="0")return miss(AT,"없음");
  else if(e.key=="/"){e.preventDefault();return $("#n"+AT)?.focus()}
  else return;
  draw(); $("#c"+AT)?.scrollIntoView({block:"center",behavior:"smooth"});
});
$("#run").onchange=load; $("#unseen").onchange=load; runs();
</script>"""


class _Handler(BaseHTTPRequestHandler):
    book: CaseBook

    def _json(self, code: int, body) -> None:
        blob = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def do_GET(self):  # noqa: N802
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        if url.path == "/":
            # 판정 이름을 화면에 심는다. `cases.py` 가 정본이라 여기 안 적는다 —
            # 두 군데 적으면 언젠가 갈리고, 갈리면 짚은 것이 안 먹는다.
            page = PAGE.replace("RIGHT_", json.dumps(RIGHT, ensure_ascii=False))
            page = page.replace("WRONG_", json.dumps(WRONG, ensure_ascii=False))
            blob = page.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)
        elif url.path == "/api/runs":
            self._json(200, {"runs": self.book.runs(), "last": self.book.last()})
        elif url.path == "/api/cases":
            run = (q.get("run") or [self.book.last()])[0]
            got = self.book.run(run)
            if (q.get("unseen") or ["0"])[0] == "1":
                got = [c for c in got if not c.seen]
            self._json(200, {
                "score": self.book.score(run),
                "cases": [{"id": c.id, "room": c.room, "thread": c.thread,
                           "body": c.body, "raw": c.raw, "parsed": c.parsed,
                           "verdict": c.verdict, "note": c.note,
                           "marks": c.marks, "missed": c.missed, "span": c.span,
                           "seconds": round(c.seconds)} for c in got],
            })
        else:
            self._json(404, {"error": "없는 자리"})

    def do_POST(self):  # noqa: N802
        길 = urllib.parse.urlparse(self.path).path
        if 길 == "/api/missed":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                d = json.loads(self.rfile.read(n).decode("utf-8"))
                got = self.book.missed(d["id"], d.get("what", "없음"))
            except (ValueError, KeyError, UnicodeDecodeError) as e:
                self._json(400, {"error": str(e)})
                return
            self._json(200, {"ok": got is not None})
            return
        if 길 != "/api/mark":
            self._json(404, {"error": "없는 자리"})
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            d = json.loads(self.rfile.read(n).decode("utf-8"))
            got = self.book.mark(d["id"], d["verdict"], d.get("note", ""),
                                 item=d.get("item", ""))
        except (ValueError, KeyError, UnicodeDecodeError) as e:
            self._json(400, {"error": str(e)})
            return
        self._json(200, {"ok": got is not None})

    def log_message(self, *a):
        pass


def serve(root: Path | str, host: str = "127.0.0.1", port: int = 8765) -> str:
    """화면을 띄운다. 돌려주는 것은 주소.

    ★ `host` 기본값이 로컬호스트다. 딴 데를 열려면 손으로 적어야 하고,
      **적는 순간 팀원들의 DM 이 그 문 밖으로 나간다.**
    """
    book = CaseBook(root)
    handler = type("Handler", (_Handler,), {"book": book})
    srv = ThreadingHTTPServer((host, port), handler)
    주소 = f"http://{host}:{srv.server_address[1]}/"
    srv.주소 = 주소
    srv.book = book
    return srv
