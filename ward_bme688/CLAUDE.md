# CLAUDE.md — ward_bme688

이 폴더에서 작업하는 Claude Code(및 개발자)를 위한 안내서입니다.
(대회 저장소 `2026ESWContest_free_THIRDIMPACT` 하위 모듈 중 **향 분류/발향(WARD)** 파트)

## 개요

Bosch **BME688** 가스센서로 **향(냄새)을 분류**하고, 서보+향수펌프 **발향부**로 향을 내보내는 임베디드 시스템.

- **핵심 문제**: Bosch 공식 툴 **BME AI-Studio**는 클래스 최대 4개 제한 → 향 3종 + 혼합 + 악취 + 공기 = 6종 이상 불가.
- **해법**: 센서 raw 가스저항을 뽑아 **Python(scikit-learn)으로 직접 분류기 학습** → 클래스 무제한.
- **하드웨어**: Bosch BME688 Development Kit(8센서) + Adafruit ESP32 Feather + (추론 허브) Jetson.

배경·설계 문서는 [docs/INDEX.md](docs/INDEX.md)에서 연결됩니다. 먼저 [docs/PLAN.md](docs/PLAN.md)와 [docs/펌웨어_정리_노션.md](docs/펌웨어_정리_노션.md)를 읽으세요.

## 폴더 구조

```
ward_bme688/
├── firmware/     ESP32/Arduino 스케치 (각 폴더 = 스케치 1개)
│   ├── bme688_sel_esp32/            [추론] ESP32 Feather에서 BSEC selectivity → WiFi/MQTT(HiveMQ) 발행 (이 모듈의 메인)
│   ├── bme688_dev_kit_logger/       [수집] 단일 HP-354 → SD CSV (10차원). scent/에 파싱·학습·라벨링 도구
│   ├── bme688_stream_full/          [수집] 단일 HP-354 전측정 시리얼 스트리밍 (SD 없이 노트북 직결)
│   ├── bme688_serial_stream/        [추론] raw를 USB 시리얼로 스트리밍 → PC sklearn 판정
│   ├── bme688_bsec_infer/           [추론] AI-Studio 4클래스 모델을 BSEC로 온디바이스 실행
│   ├── my_bme688/                   Bosch 순정 bme68x_demo_sample (BLE+datalogger, 참고 베이스)
│   └── test/                        BSEC integration 참고 C 코드
├── lib/esp32/libalgobsec.a   BSEC 2.6.1.0 precompiled 알고리즘 라이브러리(esp32)
├── receiver/mqtt_receiver.py MQTT 구독 수신 스크립트
├── python/       독립 실행 스크립트 (infer_jetson.py=Jetson MQTT 추론, serial_to_hivemq.py=시리얼→MQTT 브리지)
├── docs/         기획·펌웨어 문서 (INDEX.md가 목차)
└── aistudio/     BoardConfiguration.bmeconfig (DevKit 보드 설정)
```

> 학습 파이프라인 스크립트(parse/train/predict/plot 등 11개)는 스케치와 짝을 이뤄
> `firmware/bme688_dev_kit_logger/scent/`에 함께 있습니다.

**저장소에 없는 것**(개발 PC에만 있음): Bosch Arduino 라이브러리(Bosch-BME68x/BSEC2)와 BSEC 릴리스 → Arduino 라이브러리 매니저/Bosch에서 설치. 수집 원본 데이터(`.bmerawdata`/CSV), BME AI-Studio 프로젝트(`project.db`), BME688 데이터시트 PDF도 용량·저작권 문제로 제외.

## ★ 최우선 불변식 — 히터 프로파일 일치

**데이터 수집 때와 추론 때의 히터 온도 프로파일이 한 글자도 달라선 안 됩니다.**
프로파일이 다르면 가스센서의 "지문 모양"이 바뀌어 학습된 모델이 통째로 무효가 됩니다.

- 단일 프로파일(HP354): `tempProf={320,100,100,100,200,200,200,320,320,320}`, `mulProf={5,2,10,30,5,5,5,5,5,5}`, `MEAS_DUR=140ms`
- `bme688_serial_stream.ino`(추론)와 `bme688_dev_kit_logger.ino`(수집)는 이 값을 **동일하게** 가져야 하고, 파서 스크립트 표와도 일치해야 합니다.
- 멀티프로파일 데이터(40차원)와 단일프로파일 데이터(10차원)는 **호환 불가** — 섞으면 전면 재수집.

## 두 갈래 학습·추론 경로

| | 경로 A (커스텀, 주력) | 경로 B (Bosch 순정, 대조군) |
|---|---|---|
| 수집 | `*_logger.ino` → SD CSV | `*_logger.ino` → `.bmerawdata` |
| 학습 | `parse_devkit_*.py` → `train_scent.py`(RandomForest) | BME AI-Studio (GUI, 4클래스 한계) |
| 추론 | `bme688_serial_stream.ino` + `predict_serial.py`(PC), 또는 `bme688_sel_esp32`+`infer_jetson.py`(MQTT) | `bme688_bsec_infer.ino` (보드 온디바이스) |

## 빌드 / 실행

### 펌웨어 (Arduino IDE)
- 보드: ESP32. `firmware/<스케치>/<스케치>.ino`를 열어 업로드. 추론 스케치는 `commMux.h/.cpp` 포함 필요.
- BSEC 계열(`bme688_bsec_infer`, `bme688_sel_esp32`)은 **BSEC 2.6.1.0 필수**(다르면 `setConfig` `-34`). selectivity(IAQ_Sel) 알고리즘 사용.
- `bme688_sel_esp32`: 자격증명은 `secrets.h`(**git-ignore**) — `secrets.h.example` 복사해 채움. 상세는 [README.md](README.md).

### Python 파이프라인 (경로 A)
새 가상환경 권장. 의존성: `scikit-learn, pandas, numpy, matplotlib, joblib, pyserial, paho-mqtt`.
```powershell
# 수집 CSV → 특징 → 학습 (firmware/bme688_dev_kit_logger/scent/ 에서)
python parse_devkit_multi.py data\Air ... --label Air
python baseline_correct.py features.csv features_bc.csv    # 인접 Air 기준선 차감
python train_scent.py features_bc.csv --no-temp-hum        # RF 학습 → scent_rf.pkl + confusion_matrix.png
python predict_serial.py --port COM5                       # 실시간 추론(시리얼 모니터는 닫을 것)
```
> `serial_to_hivemq.py`/`receiver/mqtt_receiver.py`의 MQTT 자격증명은 **환경변수**(`MQTT_BROKER/USER/PASS`)로 주입. 코드에 비밀번호를 하드코딩하지 마세요.
> Windows에서 joblib 한글 경로 임시폴더 오류 → `train_scent.py`가 `JOBLIB_TEMP_FOLDER=C:\joblib_tmp`로 우회.

## 검증에서 가장 중요한 것 — GroupKFold

같은 세션의 인접 사이클은 거의 복사본이라 무작위 split 시 점수가 **가짜로 치솟습니다**.
`train_scent.py`는 `source_file`(세션) 단위로 fold를 나눠 "처음 보는 세션" 성능을 잽니다. **이 숫자가 실전·보고서용 진짜 점수.**

## 자주 밟는 함정 (교훈)

- 히터 프로파일 **수집=추론** 완전 일치(위 불변식).
- 향끼리 연달아 찍지 말고 **Air와 교대** 수집. 어기면 정확도 24%까지 폭락 사례.
- MOX 센서 날짜 단위 **드리프트** 심함 → 데모 당일 아침 재수집+재학습이 가장 확실.
- 라벨은 SD 꽂자마자 파일명에 부착(Woody/Citrus 뒤바뀐 사고 이력).
- **에탄올 베이스 향 금지**(가스 판독 간섭), 실록산 계열 회피.
- BSEC 구독은 `GAS_ESTIMATE 4개만` — RAW_GAS 섞으면 `-12` 거부. 에러: `-34`(버전)/`-12`(출력구독)/`-14`(샘플레이트).
