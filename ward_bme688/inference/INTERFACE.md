# WARD 연동 규격 — 와드 ↔ UI Jetson ↔ 주행 Jetson

와드 판정을 대시보드·주행에 잇기 위한 **메시지 형식 약속**이다. (2026-11-09 확정, 같은 날 두 Jetson 을 SSH 로 확인해 실제 주행 명령으로 고침)
결정: **MQTT → ROS 2 토픽으로 넘기고(ward_bridge_node), 출동 시작은 지금 화면 흐름(`useScentMissionNav`)이 맡는다.**

## 0. 전체 구조

```
                         핫스팟 / 인터넷
 [와드 ward-01] ──MQTT(TLS 8883)──▶ HiveMQ ◀──MQTT── [UI Jetson 192.168.10.1]  키오스크 7"
  BME688 x8 + ESP32 (배터리)                            │  React 대시보드 + server/*_bridge.py
  온디바이스 판정 (firmware/ward)                        │  Arduino(USB) = 청정기 FAN + 초음파 발향 MIST1~3
                                                        │      ← fragrance_bridge (HTTP 5174)
                                                        │  ★ ward_bridge_node (ROS 2, 이 문서 3장)
                                                        │
                                                        │ 유선 LAN · ROS 2 Humble · CycloneDDS(유니캐스트) · ROS_DOMAIN_ID 3
                                                        ▼
                                          [주행 Jetson 192.168.10.2]  ~/capstone_final_ws · 패키지 third_impact
                                           AMCL(자동 위치 찾기) · Nav2 · 정찰 + 저장 목표로 정밀 복귀
                                           받음 /go_to_goal · /go_around   보냄 /arrival_at_goal · /third_impact/status
```
- **저장 목표(goal) = 와드 위치.** 주행 쪽 `make_goal_launch.py` 로 와드 옆에 로봇을 세워 `Save Goal` 한다.
  그래서 **"와드로 출동" = `/go_to_goal`** 이다 (방 좌표 `/goal_pose` 를 따로 보내지 않는다).
- 주행 코드는 팀 저장소 `slam_ros` 가 아니라 별도 저장소 `jjong102/capstone_final_ws` (주행 담당 관리).

| 구간 | 수단 | 누가 |
|---|---|---|
| 와드 → UI Jetson | MQTT (HiveMQ 클라우드) | 와드 펌웨어 → `ward_bridge_node` |
| UI Jetson 안 | ROS 2 토픽 / HTTP(로컬) | `ward_bridge_node` → `ros_nav_bridge`(5179) → React |
| UI Jetson ↔ 주행 Jetson | ROS 2 (DDS, 유선) | `ros_nav_bridge` ↔ `third_impact` 임무 노드 (`/go_to_goal` · `/go_around` · `/arrival_at_goal`) |
| UI Jetson → 청정기·발향 | USB 시리얼 | `fragrance_bridge`(5174) → Arduino |

## 1. MQTT — 와드가 보내는 것 (구현됨)

| 토픽 | 언제 | 내용 |
|---|---|---|
| `diffuser/ward-01/inference_ward` | **판정마다 약 10초** (예열 중엔 10초마다 WARMUP) | 판정 JSON (아래) — **연동은 이것만 쓰면 된다** |
| `diffuser/ward-01/sensor` | 약 1초 | 원시 측정 CSV (녹화·분석용, 연동에는 안 씀) |
| `diffuser/ward-01/model/ack` | 모델·명령 받았을 때 | `{"result":"ok\|same\|error\|reset\|rebase\|reboot", "model":…, "msg":…}` |

QoS 0, retained 아님. **60초 동안 `inference_ward` 가 없으면 "와드 연결 끊김"** 으로 본다.

### 1-1. `inference_ward` 판정 JSON

```json
{"device_id":"ward-01","src":"ward","model":"75b9571e24","uptime_s":812,
 "state":"MALODOR","label":"Vinegar","label_note":"","mag":0.448,"strength_pct":28,"votes":8,"n":8,
 "purifier":"on","event":"ON","alert":{"malodor":true},"spraying":"","baseline_age_s":420}
```
예열 중(기준선 전)에는 짧은 형식만 온다: `{"device_id":"ward-01","src":"ward","model":"75b9571e24","uptime_s":40,"state":"WARMUP"}`

| 필드 | 값 | 뜻 · 쓰는 법 |
|---|---|---|
| `device_id` | `"ward-01"` | 와드 이름. 방 지도의 `sensor_id` 와 짝 (3-2) |
| `state` | `WARMUP` `CLEAN` `SCENT` `MALODOR` `UNKNOWN` `HOLD` | 화면 큰 글씨: 준비 중 / 깨끗함 / 향기 / 악취 / 모르는 냄새 / 약함(보류) |
| `label` | `Air` `Lavender` `Orange` `Peppermint` `Vinegar` `Unknown` `Weak` | 표결 1등 이름 |
| `label_note` | `"추정"` 또는 `""` | 향 이름은 서로 비슷해 자주 바뀜 → `SCENT` 면 "(추정)" 을 붙이거나 **분무한 채널 이름**을 띄운다 |
| `mag` | 0.0 ~ 약 1.5 (`-1` = 계산 불가) | 냄새 세기(원래 값). 0.3 이상이어야 이름을 판정 |
| `strength_pct` | 0 ~ 100 | **화면용 세기 %** — 0 = 깨끗한 공기 경계(τ_air 0.228), 100 = \|Δ\| 1.0(데모 위치에서 세 향을 진하게 뿌렸을 때). 상대 세기이지 농도(ppm)가 아니다 (11/09~ 펌웨어) |
| `spraying` | `"peppermint"` 등 / `""` | **지금 뿌리는 향** — `SPRAY <초> <이름>` 으로 받은 이름, 뿌리는 동안만. 향 이름은 센서 추정보다 이게 정확하다 |
| `votes` / `n` | 0~8 / 8 | 센서 8개 중 `label` 에 표를 준 수 (신뢰도) |
| `purifier` | `"on"` / `"off"` | 와드가 **권하는** 청정기 상태 (실제 제어는 UI Jetson) |
| `event` | `"ON"` / `"OFF"` / `""` | **바뀌는 순간에만** 값이 있다. `ON` = 출동 신호, `OFF` = 공기 회복 |
| `alert.malodor` | `true` / `false` | 켜진 상태이고 지금 판정이 악취·모르는 냄새면 true → 빨간 배너 |
| `model` | 10자리 | 와드가 쓰는 모델 id |
| `uptime_s` | 초 | 와드 켜진 시간. **줄어들면 재부팅** |
| `baseline_age_s` | 초 | 기준선(깨끗한 공기)을 잡은 지 몇 초 |

**대시보드 이름 맞추기** (11/09 기준 대시보드 `sensorLabels.js`·`mqtt_bridge.py` 의 tone, 발향 채널 `MIST_CHANNELS = [mint, lavender, orange]` = MIST1·2·3):

| 와드 `label` | 대시보드 tone | 화면 | 발향 채널 |
|---|---|---|---|
| `Air` | `fresh` | 깨끗한 공기 | — |
| `Peppermint` | `mint` | 향기 (민트 추정) | MIST1 |
| `Lavender` | `lavender` | 향기 (라벤더 추정) — **악취 아님** | MIST2 |
| `Orange` | `orange` | 향기 (오렌지 추정) | MIST3 |
| `Vinegar` | `odor` | **악취 (식초)** | — |
| `Unknown` | `odor` | 처음 보는 냄새 (출동 쪽) | — |
| `Weak` | (표시 유지) | 약한 신호 | — |

**추천 화면** — 향 이름은 센서 추정(우리 향 3종은 방향 1~4° 차이, 이름 정답률 27~92%)이라 확률 %를 붙이지 않는다.
```
향기 감지 · 세기 ███████░░░ 70%          ← strength_pct
지금 뿌리는 향: 페퍼민트                  ← spraying (없으면 "페퍼민트(추정)" = label + label_note)
악취 감지(식초) · 세기 ████████░░ 85%     ← state MALODOR
```

> ⚠ 예전 대시보드 센서(옛 분류 펌웨어)는 `lavender`/`floral` 을 "시연에선 악취" 로 썼다. 새 와드에서는 **라벤더 = 우리 향(청정기 끔), 악취 = 식초**다.
> 판정은 `state` 로 하고(`SCENT` = 향기, `MALODOR`/`UNKNOWN` = 악취 쪽), 이름은 표시용으로만 쓴다.

출동 규칙(와드 안에서 판정): 센서 8개 표결에서 악취·모르는 냄새 쪽이 **과반**이고 **3번 연속(약 30초) → `event:"ON"`**. 향·깨끗함이 12번 연속 + 켠 지 3분 → `event:"OFF"`. 향·약함은 출동시키지 않는다.

### 1-2. MQTT — 와드로 보내는 것

| 토픽 | 내용 | 상태 |
|---|---|---|
| `diffuser/ward-01/cmd` | `REBASE` (기준선 다시) / `REBOOT` — retained 금지 | 구현됨 (`tools/ward_view.py --rebase`) |
| `diffuser/ward-01/cmd` | `SPRAY <초> [이름]` (예 `SPRAY 90 peppermint`) — "지금부터 N초 향을 뿌린다" → 와드가 뿌리는 동안 + 끝나고 3분, 모르는 냄새로는 출동 안 함 (확실한 악취 클래스로만). 답장 `result:"spray"` | 구현됨 (11/09, 와드 업로드 필요) · 시험 `ward_view.py --spray 90` |
| `diffuser/ward-01/model` | 새 모델 (retained, CRC) | 구현됨 (`tools/push_model.py`) |

## 2. 출동 흐름 (화면이 맡음)

```
평소: 주행 Jetson 은 방 안을 정찰 (/go_around 상태)

와드 event:"ON" ─▶ ward_bridge_node ─/ward/event─▶ ros_nav_bridge ─HTTP─▶ React (useOdorResponse → useScentMission)
   → /go_to_goal                    정찰 취소 → 저장 목표(= 와드)로 복귀 → 정밀 정렬     (UI → 주행 Jetson)
   ← /arrival_at_goal (Empty, 한 번) 위치 오차 2cm·방향 ±5°·1초 정지 확인 뒤
   → fragrance_bridge: 청정기 ON + 발향                                                 (UI → Arduino)
     같은 때 MQTT cmd "SPRAY <초>" → 와드가 발향 중 + 3분 재출동 안 함 (보내는 쪽 = fragrance_bridge 권장)
   → 발향·청정 끝 → /go_around      정찰 재개 (도착 후 HOLD 는 /go_around 로만 풀린다)
와드 event:"OFF" ─▶ 화면: "공기 회복" 표시 (청정기 끄는 시점은 화면 흐름이 정함)
```
- **도착 뒤 순서는 UI Jetson 서버가 한다 (구현, 11/09)** — `air-scent-dashboard/server/arrival_sequence.py`(ros_nav_bridge 가 부름, 브라우저 없이 동작):
  `/arrival_at_goal` → ① `ON000` 청정 `ARRIVAL_PURIFY_SEC`(60)초 → ② `OFF<채널 9>` 발향 `ARRIVAL_SPRAY_SEC`(60)초(화면에서 마지막으로 고른 블렌드, 없으면 lavender)
  + 와드 `SPRAY <초> <이름>` → ③ `OFF000` 전부 끔 → `/go_around`. 중간 취소·정찰 재개 때도 전부 끔. 시험 `POST :5179/api/nav/arrival_sequence/test`, 로그 `/tmp/arrival_sequence.log`.
- 이미 출동 중이면 새 `ON` 은 무시한다 (와드 하나 기준). 주행 쪽도 복귀 중 `/go_around` 는 무시하고, 도착 후 HOLD 에서는 `/go_to_goal` 을 무시한다.
- `/arrival_at_goal` 은 reliable·volatile 이벤트 — `ros_nav_bridge` 가 **출동 전에 구독**하고 있어야 한다(늦게 구독하면 지난 도착을 다시 안 준다).
- 와드 `ON` 도 바뀔 때만 온다 → 화면이 늦게 켜졌으면 `/api/ward/state` 의 최근 `purifier:"on"` 으로 보완할 수 있다.
- 예전 흐름(`/goal_pose` 로 방 좌표 → `/diffusion_complete` 로 회전 멈춤)은 팀 저장소 `slam_ros` 의 옛 `all_in_one_launch` 기준이다. 지금 주행(`impact_launch`)에는 없다.

## 3. ROS 2 — `ward_bridge_node` (UI Jetson, **구현 예정**)

GDM 패키지의 `MQTT_sub_node` 를 새 와드 형식에 맞게 고치거나 같은 자리에 새로 둔다.

| 항목 | 값 |
|---|---|
| 구독 (MQTT) | `diffuser/+/inference_ward` (와드가 늘어도 그대로) — 자격증명은 이 노드만 (`MQTT_BROKER/USER/PASS`) |
| 발행 1 | **`/ward/decision`** `std_msgs/String` — 판정마다. QoS reliable, depth 10 |
| 발행 2 | **`/ward/event`** `std_msgs/String` — `event` 가 `ON`/`OFF` 일 때만. QoS reliable, depth 10, **volatile**(늦게 켜진 노드가 지난 `ON` 을 받아 엉뚱하게 출동하지 않게) |

### 3-1. 메시지 내용 (JSON 문자열)
`/ward/decision` = 와드 JSON 그대로 + 세 필드:
```json
{ …와드 JSON 전부…, "ward_id":"ward-01", "room_id":"room_1", "rx_time":"2026-11-09T15:36:22+09:00" }
```
`/ward/event` = 출동 판단에 필요한 것만:
```json
{"ward_id":"ward-01","room_id":"room_1","event":"ON","state":"MALODOR","label":"Vinegar",
 "mag":0.448,"votes":8,"n":8,"uptime_s":812,"rx_time":"2026-11-09T15:36:22+09:00"}
```

### 3-2. 와드 → 방 (화면 표시용)
출동 목적지는 주행 쪽 저장 목표(= 와드 위치)라 방 좌표가 필요 없다. 화면에 "어느 방 와드인지" 띄울 때만
방 지도(`GDM/floor_plan/*_rooms_confirmed.json`)의 **`sensor_id` 칸에 와드 이름**(`"ward-01"`)을 적어 `room_id` 를 붙인다. 못 찾으면 `room_id: null`.

### 3-3. `ros_nav_bridge` 에 더할 것 (화면용 HTTP, 구현 예정)
| 경로 | 내용 |
|---|---|
| `GET /api/ward/state` | 와드별 최근 `/ward/decision` + 마지막 수신 시각 (60초 넘으면 `stale:true`) |
| `GET /api/ward/events?since=<ms>` | 그 뒤의 `/ward/event` 목록 (화면이 1초마다 물어봄) |

## 4. 아직 정할 것
- [ ] 출동 뒤 청정기를 끄고 `/go_around` 하는 시점: 와드 `OFF` / 발향 끝 / 고정 시간 중 무엇
- [ ] 정찰 중 청정기는 켜 둘지(정찰 = 청정) 꺼 둘지 — 청정기가 돌면 와드 신호가 1/10 로 약해진다(실측)
- [x] `SPRAY <초> <이름>` 은 도착 순서(arrival_sequence)가 보낸다
- [ ] 와드가 여러 개일 때 동시 출동 순서
- [ ] 예전 `sensor/air_quality` → `/scent/raw` 경로(옛 와드 형식)는 새 와드로 바뀐 뒤 정리

---
와드 쪽 코드·판정 원리는 [README.md](README.md), 판정 규칙은 `firmware/ward/ward_infer.h`.
