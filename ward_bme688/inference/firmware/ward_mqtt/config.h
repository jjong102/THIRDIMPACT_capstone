// 와드 설정 (비밀 아님 — git 에 올라가도 됨). WiFi·브로커 계정은 secrets.h 에.
#pragma once

// 와드 이름. 와드를 여러 대 쓰면 ward-02, ward-03 … 으로 바꿔서 올린다.
#define DEVICE_ID "ward-01"

// 토픽 규칙: diffuser/{device_id}/sensor·inference·status·control
//   sensor    와드 → Jetson   원시 측정값 (이 펌웨어가 보냄)
//   inference Jetson → 대시보드 판정 결과 (ward_controller 가 보냄)
//   status    와드 → Jetson   상태 (업타임·RSSI·배터리 등, 추가 예정)
//   control   Jetson → 와드   예약 (지금은 안 씀)
// 대시보드 mqtt_bridge.py 가 구독하는 sensor/air_quality/# 와 겹치지 않는다.
#define TOPIC_PREFIX "diffuser/" DEVICE_ID
#define TOPIC_SENSOR TOPIC_PREFIX "/sensor"
#define TOPIC_STATUS TOPIC_PREFIX "/status"

// 온디바이스 판정 (ward_infer.h + model.h). 1 이면 와드가 직접 판정해 아래 토픽으로 보낸다.
//   Jetson 판정(ward_controller → /inference)과 섞이지 않게 토픽을 따로 쓴다.
//   모델을 새로 학습하면: python tools/export_model.py → model.h 갱신 → 다시 업로드.
#define WARD_INFER  1
#define TOPIC_INFER TOPIC_PREFIX "/inference_ward"

// 무선 모델 교체 (tools/push_model.py). 와드는 TOPIC_MODEL 을 구독하고, 받은 모델을 검사해
//   통과하면 내부 저장소(NVS)에 저장·적용한 뒤 TOPIC_MODEL_ACK 로 결과를 답한다. 재부팅해도 유지.
//   "WARDMODEL RESET" 을 받으면 저장된 모델을 지우고 펌웨어 기본 모델(model.h)로 돌아간다.
#define TOPIC_MODEL     TOPIC_PREFIX "/model"
#define TOPIC_MODEL_ACK TOPIC_PREFIX "/model/ack"

// WiFi 를 쓸지. 0 이면 WiFi·MQTT 를 아예 켜지 않는다 (USB 로 쓰면서 온디바이스 판정만 시리얼로 볼 때).
//   11/04: 방전된 배터리 + 팬 + WiFi 시작 전류가 겹쳐 Brownout 리셋이 반복 → USB 수집 때는 0 으로 빌드.
//   arduino-cli 로는 --build-property "build.extra_flags=-DWARD_WIFI=0" 로도 바꿀 수 있다.
#ifndef WARD_WIFI
#define WARD_WIFI 1
#endif

// WiFi 송신 출력. 기본(19.5dBm)이면 히터와 겹쳐 Brownout 리셋이 났다 → 낮춤.
//   그래도 리셋되면 WIFI_POWER_5dBm / 핫스팟을 더 가까이. 연결이 자주 끊기면 WIFI_POWER_11dBm.
#define WIFI_TX_POWER WIFI_POWER_8_5dBm

// HiveMQ 에서 같은 client id 로 두 대가 붙으면 서로 끊어 낸다 → device_id 로 구분.
#define MQTT_CLIENT_ID_STR DEVICE_ID "-raw"
