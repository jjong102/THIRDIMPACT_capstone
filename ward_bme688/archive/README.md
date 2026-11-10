# archive — 와드 예전 방식 (보관만, 쓰지 않음)

2026년 9~10월에 시도한 방식들이다. **지금 와드는 [../inference/](../inference/) 를 쓴다.**
히터 설정이 달라 여기 펌웨어로 모은 데이터는 지금 판정 모델과 호환되지 않는다.

| 폴더 | 무엇 | 바뀐 이유 |
|---|---|---|
| `firmware/bme688_sel_esp32` · `bme688_bsec_infer` · `my_bme688` | BSEC 2.6.1.0 + AI-Studio selectivity 분류 (air/woody/citrus/floral) | 클래스 4개 제한, 다른 날 정확도 낮음 → 직접 만든 각도 판정으로 교체 |
| `firmware/bme688_dev_kit_logger` · `bme688_serial_stream` · `bme688_stream_full` · `test` | SD·시리얼 수집 펌웨어 (HP-354 등 예전 히터 설정) | 수집·판정을 펌웨어 하나(`inference/firmware/ward`)로 합침 |
| `aistudio/` | AI-Studio 보드 설정 | 위와 같음 |
| `python/` · `receiver/` | 예전 Jetson 추론·MQTT 수신 스크립트 | `inference/tools/` 로 대체 |
| `docs/` | 예전 계획·가이드 (파일 이름을 영어로 바꿈 — 내용은 그대로) | `inference/README.md`·`INTERFACE.md` 로 대체 |
| `README_bsec_classifier.md` · `CLAUDE_old.md` | 예전 README·개발 안내 | |

`libalgobsec.a`(BSEC 바이너리)는 Bosch 라이선스 때문에 저장소에서 지웠다. 다시 빌드하려면 Bosch 에서 받는다.
