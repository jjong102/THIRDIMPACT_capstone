// 공기질(IAQ) 센서 설정 (비밀 아님 — git 에 올라가도 됨). WiFi·브로커 계정은 secrets.h 에.
#pragma once

// 장치 이름과 토픽. 와드(ward-01) 옆 공기질이라 와드 토픽 아래에 둔다.
#define DEVICE_ID   "iaq-01"
#define TOPIC_IAQ   "diffuser/ward-01/iaq"
#define MQTT_CLIENT_ID_STR DEVICE_ID      // HiveMQ 에서 client id 가 겹치면 서로 끊어 낸다

// 배선 (ESP32 기본 보드, I2C). BME688 모듈: VCC→3V3, GND→GND, SDA→21, SCL→22.
// 와드의 Feather 에 붙일 때는 Feather 의 SDA=23, SCL=22 로 바꾼다.
#define PIN_SDA 21
#define PIN_SCL 22
// I2C 주소: 모듈의 SDO 핀이 GND 면 0x76, 3V3 면 0x77. 0 이면 둘 다 시도한다.
#define BME_I2C_ADDR 0

// 보드 LED (ESP32 DevKitC 는 보통 GPIO2). 없으면 -1.
#define LED_PIN 2

// MQTT 발행 주기. BSEC LP 모드가 3초마다 새 값을 내므로 그 이하로 줄여도 의미 없다.
#define PUBLISH_MS 3000

// 보정 상태(BSEC state) 저장 주기. 정확도가 3 이 된 첫 순간에 한 번, 그 뒤로 이 주기마다 저장한다.
// 플래시 수명을 생각해 너무 짧게 하지 말 것 (Bosch 예제는 6시간).
#define STATE_SAVE_MS (60UL * 60UL * 1000UL)
