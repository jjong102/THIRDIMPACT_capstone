# ward_bme688 — 와드(WARD) 향기·악취 판정 센서

방에 두는 센서 장치 **와드**의 코드다. Bosch BME688 가스센서 8개(Development Kit + ESP32 Feather, 배터리)가
방 안의 냄새를 **공기 / 우리 향(페퍼민트·라벤더·오렌지) / 악취(식초)** 로 판정하고, 악취면 MQTT 로 출동 신호를 보낸다.
UI Jetson 서버가 그 신호를 받아 주행 Jetson 에 `/go_to_goal` 을 보내고, 도착하면 청정 → 발향 → 정찰 재개를 한다.

| 폴더 | 내용 | 상태 |
|---|---|---|
| **[inference/](inference/)** | **지금 쓰는 것** — 와드 펌웨어(수집·판정 하나), 판정 모델, 파이썬 도구(수집·라벨·학습·평가·모델 무선 전송) | 사용 중 |
| [inference/INTERFACE.md](inference/INTERFACE.md) | 와드 ↔ UI Jetson ↔ 주행 Jetson 메시지 규격 (MQTT 판정 JSON, 출동·도착 흐름) | 확정 (11/09) |
| [archive/](archive/) | 9~10월 예전 방식 — BSEC/AI-Studio 분류(woody/citrus/floral), SD 로거, 시험 펌웨어, 옛 문서 | 보관만 (쓰지 않음) |

시작은 [inference/README.md](inference/README.md) — 판정 원리, 폴더, 실행 순서, 성능, 모델 갱신이 정리돼 있다.

```
측정(8센서, 9.8초 사이클) → 기준선과의 차이 Δ → 세기 |Δ|·방향 → 각도로 가장 가까운 냄새 → 8센서 표결
→ 악취 3라운드(약 30초) 연속 → MQTT diffuser/ward-01/inference_ward  event:"ON"
```

- 코드 원본은 개발 폴더(git)이고, 이 폴더의 `inference/` 는 그 스냅샷이다(`inference/tools/publish_team.py` 로 올림). 이 저장소에서 직접 고치면 다음 동기화 때 덮어써질 수 있으니 담당(윤우빈)에게 알린다.
- 예전 BSEC 라이브러리 바이너리(`libalgobsec.a`)는 Bosch 라이선스(재배포 제한) 때문에 저장소에서 뺐다. 예전 펌웨어를 다시 빌드하려면 Bosch 에서 BSEC 2.6.1.0 을 받아 넣는다.
