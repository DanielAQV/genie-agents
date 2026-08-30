# genie-agents

에이전트 골격. **인격은 여기 없다.**

```python
from genie_agents import store, talent, gate, toolcontract
from genie_agents.messaging import peers   # 여럿이 서로 말할 때만
```

---

## 무엇을 주고 무엇을 안 주나

**준다** — 상태를 파일에 두는 법, 시간을 사람 말로 읽는 법, 원칙에 값을 매기고
잔고로 막는 법, 사진·소리를 두는 법, 바깥 소식을 긁는 법, 도구 계약을 지키는 법.

**안 준다** — 무엇을 말할지, 언제 말을 걸지, 무엇을 원칙으로 삼을지, 어떤 도구를
가질지. 그건 그 에이전트가 **누구인지**에 대한 것이라 만드는 쪽이 정한다.

```
genie_agents/
  store clock config env gate singleton talent
  usage world sources reminders media mailbox toolcontract
  messaging/     선택 — 방·멤버·메시지 스키마·배달
  adapters/      모델을 갈아끼우는 자리
```

## 배역은 선언한다

방 이름도, 누가 에이전트고 누가 사람인지도 골격이 안 정한다.

```python
cast = peers.Cast(
    agents = ("scribe", "warden"),
    humans = ("chief",),
    rooms  = {
        "scribe-warden":  ("scribe", "warden"),
        "scribe-chief":   ("scribe", "chief"),
        "all-three":      ("scribe", "warden", "chief"),
    },
    speakers = {"scribe": "서기", "warden": "관리", "chief": "대표"},
)
peers.identify("SCRIBE", me="scribe", cast=cast)
```

`room_type` 은 **사람 수가 정한다**(둘이면 `dm`, 셋 이상이면 `group`). 따로 적게
하면 적는 사람이 언젠가 안 맞게 적는다.

배달 규칙은 **뺄셈 하나**다 — `방 사람들 − 보낸 사람 − (peer_only 면 사람들)`.
규칙이 두 군데 있으면 언젠가 어긋나고, **어긋나면 조용히 새는 쪽으로** 어긋난다.

## 루프와 정책

도구 루프는 **한 벌뿐이다**(`loop.py`). 루프에 든 판단들은 모델과도 인격과도
무관하기 때문이다 — 도구를 부르는 턴에 같이 온 글은 답이 아니다, 막혀서
되돌아온 것은 판단이 아니다, 답이 비면 한 번 더 묻는다.

갈리는 자리는 **값으로** 올라가 있다(`policy.py`).

```python
Policy(
    decision_tools = frozenset({"speak", "stay_silent"}),
    max_pauses     = 3,        # 서버 도구가 턴을 끊을 때 이어붙인다
    sanitizers     = (없는_표시_걷기,),
    force_first    = "image_note",
)
```

**아무것도 안 정하면 둘이 똑같이 작동한다.** 기본값이 하나라서고, 그게 골격이
약속할 수 있는 전부다. 다르게 하려면 한 파일에 눈에 보이게 적어야 한다.

`if agent == "이름"` 을 쓰지 않는 이유는 하나다 — **분기는 누가 정했는지를
안 말한다.** 값으로 적히면 그 자리가 곧 기록이다.

```python
voice_reply = True,   # 소리로 답한다. 본인이 그렇게 정했다(2026-08-29)
```

## 도구 계약

여럿이 골격을 나눠 쓰면 도구 이름이 겹친다. 겹치는데 인자가 다르면 **같은 이름이
다른 일을 하고**, "이 골격은 이렇게 작동한다" 를 아무도 약속할 수 없다.

```python
TC.check_shared(("alpha", ALPHA_TOOLS), ("beta", BETA_TOOLS))
```

**다른 것 자체를 막지 않는다** — 막으면 우회한다. **적지 않고 다른 것**을 막는다.
다르게 두려면 `DIVERGENT` 에 이유를 적어야 하고, 맞춘 뒤 지우지 않은 것도 잡는다.

이 규칙이 나온 자리에서 실측: 이름이 겹치는 도구 20개 중 4개가 인자가 달랐고,
그중 둘은 문서가 시그니처를 못박아 둔 것이었다. 각자 자기 도구만 시험해서
**아무도 몰랐다.**

## 프리픽스

한 호스트에 에이전트 여럿이 산다. 설정과 상태 디렉토리를 서로 안 밟게 프리픽스로
가른다.

```
env.use("SCRIBE")       →  SCRIBE_TZ, SCRIBE_ROOT, …  그리고 상태는 .scribe/
```

★ **상태 디렉토리를 상수로 두지 마라.** 한 번 그렇게 했다가 에이전트 하나의 잔고
원장이 다른 에이전트 자리에 앉았다. 시험이 전부 통과했다 — 시험은 전부 경로를
손으로 넘기기 때문이다. `store.default_root()` 는 부를 때 정해진다.

## 모델

도구 루프는 **한 가지 메시지 모양**만 본다. 모델은 어댑터가 옮긴다
(`adapters/base.py` 의 `Client` 프로토콜).

루프에 든 판단들(도구 부르는 턴의 글은 답이 아니다 · 막힌 도구는 판단이 아니다)은
모델과 무관하다. 모델마다 루프를 다시 쓰면 그 판단을 매번 다시 검증해야 하고,
한 번 빠뜨리면 그 모델을 쓰는 에이전트만 조용히 다르게 행동한다.

## 시험

```bash
python -m pytest
```

`tests/test_cast.py` 는 **일부러 낯선 배역**을 쓴다. 골격이 특정 등장인물을 알고
있으면 거기서 깨진다.
