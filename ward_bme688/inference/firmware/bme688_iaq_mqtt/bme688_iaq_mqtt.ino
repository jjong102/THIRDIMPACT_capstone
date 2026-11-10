/**
 * bme688_iaq_mqtt.ino
 *
 * 공기질 표시용 — 단일 BME688 + ESP32 기본 보드에서 Bosch BSEC IAQ 를 돌려 MQTT 로 보낸다.
 * 와드(DevKit 8센서)와는 별개 장치다. 향 판정에는 쓰지 않고 대시보드 표시만 한다.
 *
 *   [BME688 ─I2C─ ESP32] BSEC IAQ (LP, 3초) ── MQTT "diffuser/ward-01/iaq" ──→ 대시보드 / iaq_view.py
 *
 * 보내는 값 (JSON, PUBLISH_MS 마다)
 *   iaq        0~500, 클수록 나쁨. iaq_acc 가 3 이 되기 전에는 믿지 말 것
 *   iaq_acc    0 = 시작 직후(값 무의미) / 1 = 보정 중 / 2 = 거의 됨 / 3 = 보정 완료
 *   static_iaq 보정 범위를 고정한 IAQ (장소를 옮겨도 비교 가능)
 *   co2_eq     CO2 환산 ppm (VOC 로 추정한 값이지 실제 CO2 센서가 아님)
 *   bvoc_eq    호흡 VOC 환산 ppm
 *   temp, hum  자가 발열을 보정한 온도·습도
 *   level      Bosch IAQ 표의 단계 (excellent ~ extreme)
 *   stab, run_in  센서 안정화 / 초기 길들이기 끝났는지 (1 = 끝)
 *
 * ★ 보정에 시간이 걸린다. 깨끗한 공기와 오염된 공기를 몇 시간 겪어야 iaq_acc 가 3 이 된다.
 *   보정 상태는 NVS(내부 플래시)에 저장해 두므로 전원을 껐다 켜도 이어진다.
 *   데모 전날까지 계속 켜 두는 것을 권장한다.
 *
 * 준비
 *   1. Arduino IDE 스케치북 = C:\2026capston (libraries/ 의 Bosch-BSEC2·BME68x·PubSubClient 사용)
 *   2. 보드: "ESP32 Dev Module", ESP32 코어 2.0.17. "Sketch too big" 이 나면 Partition Scheme → Huge APP
 *   3. secrets.h.example → secrets.h 로 복사해 WiFi·HiveMQ 값 채우기 (ward_mqtt 의 것과 같음)
 *   4. 배선·주소·토픽은 config.h
 *
 * BSEC: 라이브러리의 libalgobsec.a 는 2.6.1.0 IAQ_Sel 변형(IAQ 지원). 설정은 3.3V·3초(LP)·4일 히스토리.
 * LED: 1초 점멸 = MQTT 연결됨 / 0.25초 점멸 = 연결 안 됨 / 켜진 채 = 센서 오류
 */

#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <Preferences.h>
#include <bsec2.h>
#include "secrets.h"
#include "config.h"

/* 3.3V 공급 + LP(3초) 용 Bosch 설정. 1.8V 기본값으로 돌리면 자가 발열 보정이 틀어진다. */
const uint8_t bsec_config[] = {
#include "config/bme688/bme688_sel_33v_3s_4d/bsec_selectivity.txt"
};

const uint16_t MQTT_PORT = 8883;

Bsec2            envSensor;
WiFiClientSecure espClient;
PubSubClient     mqtt(espClient);
Preferences      prefs;

/* 최신 측정값 — 센서 콜백(core 1)이 쓰고 MQTT 태스크(core 0)가 읽는다 */
struct Reading {
  float iaq = NAN, static_iaq = NAN, co2 = NAN, bvoc = NAN, temp = NAN, hum = NAN, press = NAN, gas = NAN;
  uint8_t acc = 0, stab = 0, run_in = 0;
  uint32_t ms = 0;     /* 이 값을 받은 시각 (millis) */
  uint32_t n = 0;      /* 누적 측정 횟수 */
};
static Reading       g_last;
static portMUX_TYPE  g_mux = portMUX_INITIALIZER_UNLOCKED;
static volatile bool g_mqtt_ok = false;
static volatile bool g_sensor_ok = false;
static uint8_t       g_state[BSEC_MAX_STATE_BLOB_SIZE];
static uint32_t      g_lastSave = 0;
static bool          g_savedAtAcc3 = false;

/* ---------- 보정 상태 저장/복원 (NVS) ---------- */
static bool loadState()
{
  prefs.begin("bsec", true);
  size_t n = prefs.getBytesLength("state");
  bool ok = false;
  if (n == BSEC_MAX_STATE_BLOB_SIZE) {
    prefs.getBytes("state", g_state, n);
    ok = envSensor.setState(g_state);
    Serial.println(ok ? F("# 보정 상태 복원 (이전 보정 이어서)") : F("# 보정 상태 복원 실패 → 처음부터 보정"));
  } else {
    Serial.println(F("# 저장된 보정 상태 없음 → 처음부터 보정 (iaq_acc 가 3 이 될 때까지 몇 시간)"));
  }
  prefs.end();
  return ok;
}

static void saveState()
{
  if (!envSensor.getState(g_state)) {
    Serial.println(F("# 보정 상태 읽기 실패"));
    return;
  }
  prefs.begin("bsec", false);
  prefs.putBytes("state", g_state, BSEC_MAX_STATE_BLOB_SIZE);
  prefs.end();
  g_lastSave = millis();
  Serial.println(F("# 보정 상태 저장 (NVS)"));
}

/* ---------- BSEC 결과 ---------- */
static void newDataCallback(const bme68xData data, const bsecOutputs outputs, const Bsec2 bsec)
{
  (void)data; (void)bsec;
  if (!outputs.nOutputs) return;
  Reading r;
  portENTER_CRITICAL(&g_mux);
  r = g_last;
  portEXIT_CRITICAL(&g_mux);

  for (uint8_t i = 0; i < outputs.nOutputs; i++) {
    const bsecData o = outputs.output[i];
    switch (o.sensor_id) {
      case BSEC_OUTPUT_IAQ:                 r.iaq = o.signal; r.acc = o.accuracy; break;
      case BSEC_OUTPUT_STATIC_IAQ:          r.static_iaq = o.signal; break;
      case BSEC_OUTPUT_CO2_EQUIVALENT:      r.co2 = o.signal; break;
      case BSEC_OUTPUT_BREATH_VOC_EQUIVALENT: r.bvoc = o.signal; break;
      case BSEC_OUTPUT_SENSOR_HEAT_COMPENSATED_TEMPERATURE: r.temp = o.signal; break;
      case BSEC_OUTPUT_SENSOR_HEAT_COMPENSATED_HUMIDITY:    r.hum = o.signal; break;
      case BSEC_OUTPUT_RAW_PRESSURE:        r.press = o.signal; break;
      case BSEC_OUTPUT_RAW_GAS:             r.gas = o.signal; break;
      case BSEC_OUTPUT_STABILIZATION_STATUS: r.stab = (uint8_t)o.signal; break;
      case BSEC_OUTPUT_RUN_IN_STATUS:       r.run_in = (uint8_t)o.signal; break;
      default: break;
    }
  }
  r.ms = millis();
  r.n++;
  portENTER_CRITICAL(&g_mux);
  g_last = r;
  portEXIT_CRITICAL(&g_mux);

  /* 정확도 3 에 처음 닿으면 바로 한 번, 그 뒤로는 STATE_SAVE_MS 마다 저장 */
  if (r.acc >= 3 && (!g_savedAtAcc3 || millis() - g_lastSave >= STATE_SAVE_MS)) {
    saveState();
    g_savedAtAcc3 = true;
  }
}

static const char *iaqLevel(float iaq)
{
  if (isnan(iaq))  return "unknown";
  if (iaq <= 50)   return "excellent";
  if (iaq <= 100)  return "good";
  if (iaq <= 150)  return "lightly_polluted";
  if (iaq <= 200)  return "moderately_polluted";
  if (iaq <= 250)  return "heavily_polluted";
  if (iaq <= 350)  return "severely_polluted";
  return "extremely_polluted";
}

/* NaN 은 JSON 에서 null 로 */
static int fmtNum(char *buf, size_t sz, const char *key, float v, int digits)
{
  if (isnan(v)) return snprintf(buf, sz, "\"%s\":null,", key);
  return snprintf(buf, sz, "\"%s\":%.*f,", key, digits, v);
}

static size_t buildJson(char *buf, size_t sz, const Reading &r)
{
  size_t n = snprintf(buf, sz, "{\"device_id\":\"%s\",\"uptime_s\":%lu,\"n\":%lu,\"age_ms\":%lu,",
                      DEVICE_ID, (unsigned long)(millis() / 1000), (unsigned long)r.n,
                      (unsigned long)(millis() - r.ms));
  n += fmtNum(buf + n, sz - n, "iaq", r.iaq, 1);
  n += snprintf(buf + n, sz - n, "\"iaq_acc\":%u,", r.acc);
  n += fmtNum(buf + n, sz - n, "static_iaq", r.static_iaq, 1);
  n += fmtNum(buf + n, sz - n, "co2_eq", r.co2, 0);
  n += fmtNum(buf + n, sz - n, "bvoc_eq", r.bvoc, 2);
  n += fmtNum(buf + n, sz - n, "temp", r.temp, 2);
  n += fmtNum(buf + n, sz - n, "hum", r.hum, 2);
  n += fmtNum(buf + n, sz - n, "press_hpa", isnan(r.press) ? NAN : r.press / 100.0f, 1);
  n += fmtNum(buf + n, sz - n, "gas_ohm", r.gas, 0);
  n += snprintf(buf + n, sz - n, "\"stab\":%u,\"run_in\":%u,\"rssi\":%d,\"level\":\"%s\"}",
                r.stab, r.run_in, (int)WiFi.RSSI(), iaqLevel(r.iaq));
  return n;
}

/* ---------- 네트워크 (core 0) — 연결이 오래 걸려도 BSEC 타이밍(core 1)을 막지 않게 분리 ---------- */
static void mqtt_task(void *arg)
{
  (void)arg;
  static char json[512];
  uint32_t lastPub = 0;
  for (;;) {
    if (WiFi.status() != WL_CONNECTED) {
      g_mqtt_ok = false;
      WiFi.mode(WIFI_STA);
      WiFi.begin(SECRET_WIFI_SSID, SECRET_WIFI_PASS);
      for (int i = 0; i < 20 && WiFi.status() != WL_CONNECTED; i++) vTaskDelay(pdMS_TO_TICKS(500));
      continue;
    }
    if (!mqtt.connected()) {
      g_mqtt_ok = false;
      espClient.setInsecure();               /* 서버 인증서 검증 생략 (다른 데모 펌웨어와 동일) */
      espClient.setTimeout(5);
      mqtt.setServer(SECRET_MQTT_BROKER, MQTT_PORT);
      mqtt.setKeepAlive(60);
      mqtt.setSocketTimeout(5);
      if (mqtt.connect(MQTT_CLIENT_ID_STR, SECRET_MQTT_USER, SECRET_MQTT_PASS)) {
        Serial.print(F("# MQTT 연결됨 → ")); Serial.println(F(TOPIC_IAQ));
      } else {
        vTaskDelay(pdMS_TO_TICKS(2000));
        continue;
      }
    }
    g_mqtt_ok = true;
    mqtt.loop();
    if (millis() - lastPub >= PUBLISH_MS) {
      lastPub = millis();
      Reading r;
      portENTER_CRITICAL(&g_mux);
      r = g_last;
      portEXIT_CRITICAL(&g_mux);
      if (r.n > 0) {
        size_t n = buildJson(json, sizeof(json), r);
        if (!mqtt.publish(TOPIC_IAQ, (const uint8_t *)json, n, false))
          Serial.println(F("# MQTT 발행 실패"));
      }
    }
    vTaskDelay(pdMS_TO_TICKS(50));
  }
}

static void led_task(void *arg)
{
  (void)arg;
  if (LED_PIN < 0) vTaskDelete(NULL);
  pinMode(LED_PIN, OUTPUT);
  bool on = false;
  for (;;) {
    if (!g_sensor_ok) { digitalWrite(LED_PIN, HIGH); vTaskDelay(pdMS_TO_TICKS(500)); continue; }
    on = !on;
    digitalWrite(LED_PIN, on);
    vTaskDelay(pdMS_TO_TICKS(g_mqtt_ok ? 1000 : 250));
  }
}

static bool reportBsec(const char *where)
{
  if (envSensor.status < BSEC_OK || envSensor.sensor.status < BME68X_OK) {
    Serial.printf("# %s 실패: BSEC %d / BME68X %d\n", where, envSensor.status, envSensor.sensor.status);
    return false;
  }
  if (envSensor.status > BSEC_OK || envSensor.sensor.status > BME68X_OK)
    Serial.printf("# %s 경고: BSEC %d / BME68X %d\n", where, envSensor.status, envSensor.sensor.status);
  return true;
}

static bool startSensor()
{
  const uint8_t addrs[2] = {BME68X_I2C_ADDR_LOW, BME68X_I2C_ADDR_HIGH};   /* 0x76, 0x77 */
  bool found = false;
  for (uint8_t i = 0; i < 2 && !found; i++) {
    uint8_t a = addrs[i];
    if (BME_I2C_ADDR && a != BME_I2C_ADDR) continue;
    if (envSensor.begin(a, Wire)) {
      Serial.printf("# BME688 찾음: I2C 0x%02X (SDA %d, SCL %d)\n", a, PIN_SDA, PIN_SCL);
      found = true;
    }
  }
  if (!found) {
    Serial.println(F("# BME688 을 못 찾음 — 배선(SDA/SCL/3V3/GND)과 config.h 의 핀·주소를 확인"));
    return false;
  }
  if (!envSensor.setConfig(bsec_config)) return reportBsec("setConfig (-34 면 BSEC 버전 불일치)");
  loadState();
  envSensor.setTemperatureOffset(TEMP_OFFSET_LP);

  bsecSensor list[] = {
    BSEC_OUTPUT_IAQ, BSEC_OUTPUT_STATIC_IAQ, BSEC_OUTPUT_CO2_EQUIVALENT, BSEC_OUTPUT_BREATH_VOC_EQUIVALENT,
    BSEC_OUTPUT_SENSOR_HEAT_COMPENSATED_TEMPERATURE, BSEC_OUTPUT_SENSOR_HEAT_COMPENSATED_HUMIDITY,
    BSEC_OUTPUT_RAW_PRESSURE, BSEC_OUTPUT_RAW_GAS,
    BSEC_OUTPUT_STABILIZATION_STATUS, BSEC_OUTPUT_RUN_IN_STATUS,
  };
  if (!envSensor.updateSubscription(list, ARRAY_LEN(list), BSEC_SAMPLE_RATE_LP))
    return reportBsec("updateSubscription");
  envSensor.attachCallback(newDataCallback);
  Serial.printf("# BSEC %d.%d.%d.%d  LP(3초)  토픽 %s\n", envSensor.version.major, envSensor.version.minor,
                envSensor.version.major_bugfix, envSensor.version.minor_bugfix, TOPIC_IAQ);
  return true;
}

void setup()
{
  Serial.begin(115200);
  delay(300);
  Serial.println(F("\n# bme688_iaq_mqtt — 공기질(IAQ) 표시용"));
  Wire.begin(PIN_SDA, PIN_SCL);
  mqtt.setBufferSize(768);
  xTaskCreatePinnedToCore(led_task, "led", 2048, NULL, 1, NULL, 0);
  xTaskCreatePinnedToCore(mqtt_task, "mqtt", 8192, NULL, 1, NULL, 0);
  g_sensor_ok = startSensor();
}

void loop()
{
  if (!g_sensor_ok) {            /* 센서를 못 찾았으면 10초마다 다시 시도 */
    delay(10000);
    g_sensor_ok = startSensor();
    return;
  }
  if (!envSensor.run()) reportBsec("run");

  static uint32_t lastPrint = 0;   /* 시리얼에도 10초마다 한 줄 */
  if (millis() - lastPrint >= 10000) {
    lastPrint = millis();
    Reading r;
    portENTER_CRITICAL(&g_mux);
    r = g_last;
    portEXIT_CRITICAL(&g_mux);
    if (r.n)
      Serial.printf("IAQ %.0f (acc %u, %s)  CO2eq %.0f ppm  bVOC %.2f ppm  %.1fC %.0f%%  MQTT %s\n",
                    r.iaq, r.acc, iaqLevel(r.iaq), r.co2, r.bvoc, r.temp, r.hum, g_mqtt_ok ? "OK" : "끊김");
  }
}
