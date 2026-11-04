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

// HiveMQ 에서 같은 client id 로 두 대가 붙으면 서로 끊어 낸다 → device_id 로 구분.
#define MQTT_CLIENT_ID_STR DEVICE_ID "-raw"
