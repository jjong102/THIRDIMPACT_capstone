/**
 * bme688_demo_air_vs_scent.ino
 *
 * 부스 시연용 — 향 "종류"가 아니라 "있음 / 없음"만 판정하는 초단순·고신뢰 데모.
 *   공기(CLEAN AIR)  vs  향 감지(SCENT DETECTED)   2진 판정.
 *
 * 왜 이렇게?
 *   다중 향 분류 정확도가 아직 낮아, 시연에서는 확실히 동작하는 "향 감지" 하나로 승부.
 *   BME688 은 환원성 가스(대부분의 방향·VOC)에 노출되면 가스저항이 "떨어진다".
 *   → 공기일 때의 저항을 기준선으로 잡아두고, 저항이 충분히 떨어지면 "향 감지".
 *   히터 프로파일이나 학습 모델과 무관하게, 기준선 대비 상대변화만 보므로 강건하다.
 *
 * === 사용법 ===
 *   1. 발향을 끈(공기) 상태로 전원/업로드. 시리얼 모니터 115200.
 *   2. 안정화 5초 → 공기 기준선 25초 수집(이 동안 향 넣지 말 것!) → 판정 시작.
 *   3. 향을 가까이 대면  ">>> 향 감지" 로 전환, 치우면 다시 "공기".
 *   4. 언제든 시리얼로 'r' 입력 → 기준선 재보정(발향 끈 상태에서).
 *
 * === LED 신호 (DevKit 내장 LED) ===
 *   빠른 점멸  = 기준선 수집(보정) 중
 *   느린 점멸  = 공기 (정상 대기)
 *   계속 켜짐  = 향 감지
 *
 * 하드웨어: Bosch BME688 DevKit(8센서, ESP32) + commMux. serial_stream 스케치와 동일 구성.
 */

#include "Arduino.h"
#include "bme68xLibrary.h"
#include "commMux.h"

/* ---- 네트워킹 (WiFi + HiveMQ MQTT over TLS) ---- */
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
/* 자격증명은 secrets.h (git-ignore). secrets.h.example 복사해 값 채우기. */
#include "secrets.h"

#define N_KIT_SENS 8
#define N_STEP     10
#define LED_PIN    LED_BUILTIN
#define MEAS_DUR   140          /* 스텝당 측정 주기(ms) */
#define BAUD       115200

/* ---- 튜닝 파라미터 (필요하면 여기만 조정) ------------------------------- */
const uint32_t SETTLE_MS    = 5000;    /* 전원 직후 안정화(무시) 구간 */
const uint32_t BOOTSTRAP_MS = 25000;   /* 공기 기준선 수집 시간 — 이 동안 향 금지! */
const uint32_t DECISION_MS  = 1000;    /* 판정/출력 주기 */
const float    THRESH_ON    = 0.08f;   /* 기준선 대비 log10 저항 하락이 이 이상 → 향 */
const float    THRESH_OFF   = 0.04f;   /* 이 이하로 회복 → 공기 (히스테리시스로 깜빡임 방지) */
const float    STRONG_RESP  = 0.40f;   /* 세기 100% 로 볼 하락량(log10) */
const float    DRIFT_ALPHA  = 0.02f;   /* 공기 안정 시 기준선 추종 속도(드리프트 보정) */
const float    CUR_ALPHA    = 0.5f;    /* 현재값 EMA 평활 계수(노이즈 완화) */
const uint8_t  MIN_PAIRS    = 6;       /* 판정에 필요한 최소 유효 (센서,스텝) 쌍 수 */
/* ----------------------------------------------------------------------- */

Bme68x     bme[N_KIT_SENS];
commMux    commSetup[N_KIT_SENS];
bme68xData d;
bool       sensorOk[N_KIT_SENS] = {false};

/* 기준선(공기) 및 현재값 — 스텝은 온도가 달라 저항대가 다르므로 (센서,스텝)별로 관리 */
float    baseSum[N_KIT_SENS][N_STEP];
uint16_t baseCnt[N_KIT_SENS][N_STEP];
float    baseline[N_KIT_SENS][N_STEP];
bool     baseValid[N_KIT_SENS][N_STEP];
float    curLog[N_KIT_SENS][N_STEP];
bool     curValid[N_KIT_SENS][N_STEP];

enum Phase { SETTLE, BOOTSTRAP, RUN };
Phase    phase = SETTLE;
uint32_t phaseStart = 0;
uint32_t lastMeas = 0, lastDecision = 0, lastBlink = 0;
bool     scent = false;
bool     led = false;

/* ===== MQTT (HiveMQ Cloud, TLS 8883) =====================================
 * bme688_sel_esp32 의 검증된 네트워킹 구조를 그대로 사용:
 *  - 네트워킹은 core 0 전용 태스크(mqtt_task)에서만 처리 → 측정/판정 루프를 안 막음
 *  - 판정할 때마다 최신 상태를 g_payload 에 담아두면 태스크가 ~1초마다 발행
 * 발행 형식:  "air (0%)" / "scent (84%)"  ← 기존 수신기/팀 대시보드 정규식과 호환
 * ------------------------------------------------------------------------- */
const char    *WIFI_SSID      = SECRET_WIFI_SSID;
const char    *WIFI_PASS      = SECRET_WIFI_PASS;
const char    *MQTT_BROKER    = SECRET_MQTT_BROKER;
const uint16_t MQTT_PORT      = 8883;
const char    *MQTT_USER      = SECRET_MQTT_USER;
const char    *MQTT_PASS      = SECRET_MQTT_PASS;
const char    *MQTT_TOPIC     = "sensor/air_quality/ward_demo"; /* 하위토픽으로 분리 → sel_esp32와 안 겹침, 대시보드는 #로 수신 */
const char    *MQTT_CLIENT_ID = "ESP32_WARD_DEMO";

WiFiClientSecure espClient;
PubSubClient     mqtt(espClient);

char          g_payload[64] = {0};
volatile bool g_have_payload = false;
portMUX_TYPE  g_payload_mux = portMUX_INITIALIZER_UNLOCKED;

/* 현재 상태를 발행용 페이로드로 저장 (실제 발행은 네트워킹 태스크가 함)
 * 형식 "라벨 (NN%)" — 대시보드 정규식 및 mqtt_receiver.py 와 호환 */
void publishState(float resp, int strength)
{
  (void)resp;
  char pbuf[32];
  snprintf(pbuf, sizeof(pbuf), "%s (%d%%)",
           scent ? "scent" : "air", strength);
  portENTER_CRITICAL(&g_payload_mux);
  strncpy(g_payload, pbuf, sizeof(g_payload));
  g_payload[sizeof(g_payload) - 1] = '\0';
  g_have_payload = true;
  portEXIT_CRITICAL(&g_payload_mux);
}

/* 비블로킹 WiFi (재)접속: 즉시 반환 → 핫스팟이 없어도 루프가 멈추지 않음 */
static void wifi_connect()
{
  if (WiFi.status() == WL_CONNECTED) return;
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
}

/* WiFi 가 붙어 있으면 MQTT/TLS 한 번 시도 (소켓 타임아웃으로 무한 대기 방지) */
static void mqtt_connect()
{
  if (mqtt.connected()) return;
  if (WiFi.status() != WL_CONNECTED) return;
  espClient.setInsecure();        /* 서버 인증서 검증 생략 (CERT_NONE 과 동일) */
  espClient.setTimeout(5);
  mqtt.setServer(MQTT_BROKER, MQTT_PORT);
  mqtt.setKeepAlive(60);
  mqtt.setSocketTimeout(5);
  mqtt.connect(MQTT_CLIENT_ID, MQTT_USER, MQTT_PASS);
}

/* core 0 전용 네트워킹 태스크: 접속/재접속 + 최신 페이로드 ~1초마다 발행 */
static void mqtt_task(void *arg)
{
  (void)arg;
  bool wasConnected = false;
  for (;;) {
    bool now = mqtt.connected();
    if (now && !wasConnected) {
      Serial.print(F("# MQTT 연결됨 → 발행 시작 (topic="));
      Serial.print(MQTT_TOPIC); Serial.println(F(")"));
    } else if (!now && wasConnected) {
      Serial.println(F("# MQTT 끊김 → 재접속 시도"));
    }
    wasConnected = now;

    if (WiFi.status() != WL_CONNECTED)      wifi_connect();
    else if (!now)                          mqtt_connect();
    else {
      mqtt.loop();
      if (g_have_payload) {
        char buf[64];
        portENTER_CRITICAL(&g_payload_mux);
        strncpy(buf, g_payload, sizeof(buf));
        portEXIT_CRITICAL(&g_payload_mux);
        buf[sizeof(buf) - 1] = '\0';
        mqtt.publish(MQTT_TOPIC, buf);
      }
    }
    vTaskDelay(pdMS_TO_TICKS(1000));   /* 약 1초마다 발행/재접속 시도 */
  }
}
/* ======================================================================== */

void clearBaseline()
{
  for (uint8_t s = 0; s < N_KIT_SENS; s++)
    for (uint8_t k = 0; k < N_STEP; k++) {
      baseSum[s][k] = 0.0f; baseCnt[s][k] = 0;
      baseline[s][k] = 0.0f; baseValid[s][k] = false;
      curLog[s][k] = 0.0f;  curValid[s][k] = false;
    }
  scent = false;
}

void startCalibration(const __FlashStringHelper *why)
{
  clearBaseline();
  phase = SETTLE;
  phaseStart = millis();
  Serial.println(why);
  Serial.println(F("# (발향 끈 공기 상태를 유지하세요)"));
}

void setup(void)
{
  Serial.begin(BAUD);
  pinMode(LED_PIN, OUTPUT);
  commMuxBegin(Wire, SPI);
  delay(200);

  /* 네트워킹은 core 0 전용 태스크에서 처리 → 측정 루프(core 1)를 절대 막지 않음.
   * 센서 예열/기준선 수집하는 동안 백그라운드로 WiFi/MQTT 가 붙는다. */
  xTaskCreatePinnedToCore(mqtt_task, "mqtt", 16384, NULL, 1, NULL, 0);

  Serial.println();
  Serial.println(F("# ============================================"));
  Serial.println(F("#  BME688 데모 — 공기 vs 향 감지 (WARD)"));
  Serial.println(F("# ============================================"));

  uint8_t ok = 0;
  for (uint8_t i = 0; i < N_KIT_SENS; i++) {
    commSetup[i] = commMuxSetConfig(Wire, SPI, i, commSetup[i]);
    bme[i].begin(BME68X_SPI_INTF, commMuxRead, commMuxWrite, commMuxDelay,
                 &commSetup[i]);
    if (bme[i].checkStatus()) {
      Serial.print(F("# 경고: 센서 ")); Serial.print(i);
      Serial.print(F(" 초기화 실패 — 건너뜀 ("));
      Serial.print(bme[i].statusString()); Serial.println(F(")"));
      sensorOk[i] = false;
      continue;
    }
    /* 히터 프로파일: 기존 스케치와 동일한 HP354 (검증된 값) */
    bme[i].setTPH();
    uint16_t tempProf[10] = {320, 100, 100, 100, 200, 200, 200, 320, 320, 320};
    uint16_t mulProf[10]  = {5, 2, 10, 30, 5, 5, 5, 5, 5, 5};
    uint16_t sharedHeatrDur =
        MEAS_DUR - (bme[i].getMeasDur(BME68X_PARALLEL_MODE) / INT64_C(1000));
    bme[i].setHeaterProf(tempProf, mulProf, sharedHeatrDur, 10);
    bme[i].setOpMode(BME68X_PARALLEL_MODE);
    sensorOk[i] = true;
    ok++;
  }

  if (ok == 0) {
    Serial.println(F("# 오류: 동작하는 센서가 없습니다. 배선/전원 확인."));
    while (1) { digitalWrite(LED_PIN, HIGH); delay(150);
                digitalWrite(LED_PIN, LOW);  delay(150); }
  }
  Serial.print(F("# 동작 센서 ")); Serial.print(ok); Serial.println(F(" / 8"));
  Serial.println(F("# 명령: 'r' 입력 = 기준선 재보정"));

  startCalibration(F("# 안정화 후 공기 기준선을 수집합니다..."));
}

/* 측정 1스텝 처리: 유효한 (센서,스텝) 값을 단계에 맞게 반영 */
void handleReading(uint8_t s, uint8_t step, float gas)
{
  float lg = log10(gas);
  if (phase == BOOTSTRAP) {
    baseSum[s][step] += lg;
    baseCnt[s][step] += 1;
  } else if (phase == RUN) {
    if (curValid[s][step])
      curLog[s][step] = (1.0f - CUR_ALPHA) * curLog[s][step] + CUR_ALPHA * lg;
    else
      curLog[s][step] = lg;
    curValid[s][step] = true;
  }
}

void finalizeBaseline()
{
  uint16_t pairs = 0;
  for (uint8_t s = 0; s < N_KIT_SENS; s++)
    for (uint8_t k = 0; k < N_STEP; k++)
      if (baseCnt[s][k] > 0) {
        baseline[s][k] = baseSum[s][k] / baseCnt[s][k];
        baseValid[s][k] = true;
        pairs++;
      }
  Serial.print(F("# 기준선 완료 (유효 지점 "));
  Serial.print(pairs);
  Serial.println(F("개). 판정 시작 — 향을 대보세요."));
}

void decide()
{
  float sum = 0.0f;
  uint16_t n = 0;
  for (uint8_t s = 0; s < N_KIT_SENS; s++)
    for (uint8_t k = 0; k < N_STEP; k++)
      if (sensorOk[s] && baseValid[s][k] && curValid[s][k]) {
        sum += (baseline[s][k] - curLog[s][k]);   /* + = 저항 하락 = 향 */
        n++;
      }

  if (n < MIN_PAIRS) {
    Serial.println(F("# ...센서 데이터 모으는 중"));
    return;
  }

  float resp = sum / n;

  /* 히스테리시스 판정 */
  if (!scent && resp >= THRESH_ON) {
    scent = true;
    Serial.println(F(">>> 향 감지  (SCENT DETECTED)"));
  } else if (scent && resp <= THRESH_OFF) {
    scent = false;
    Serial.println(F(">>> 공기     (CLEAN AIR)"));
  }

  /* 공기이고 변화가 거의 없을 때만 기준선을 천천히 따라가 드리프트 보정 */
  if (!scent && resp < THRESH_OFF) {
    for (uint8_t s = 0; s < N_KIT_SENS; s++)
      for (uint8_t k = 0; k < N_STEP; k++)
        if (baseValid[s][k] && curValid[s][k])
          baseline[s][k] = (1.0f - DRIFT_ALPHA) * baseline[s][k]
                         + DRIFT_ALPHA * curLog[s][k];
  }

  /* 세기(%) — 데모 화면용 대략치 */
  int strength = (int)((resp / STRONG_RESP) * 100.0f);
  if (strength < 0) strength = 0;
  if (strength > 100) strength = 100;

  /* MQTT 발행용 최신 상태 갱신 (네트워킹 태스크가 ~1초마다 실제 발행) */
  publishState(resp, strength);

  Serial.print(F("# ["));
  Serial.print(scent ? F("향") : F("공기"));
  Serial.print(F("] resp="));
  Serial.print(resp, 3);
  Serial.print(F("  세기="));
  Serial.print(strength);
  Serial.print(F("%  pairs="));
  Serial.println(n);
}

void updateLed()
{
  uint32_t interval;
  if (phase != RUN)      interval = 150;    /* 보정 중: 빠른 점멸 */
  else if (scent)        interval = 0;      /* 향 감지: 계속 켜짐 */
  else                   interval = 1000;   /* 공기: 느린 점멸 */

  if (interval == 0) {
    digitalWrite(LED_PIN, HIGH);
    return;
  }
  if (millis() - lastBlink >= interval) {
    lastBlink = millis();
    led = !led;
    digitalWrite(LED_PIN, led);
  }
}

void loop(void)
{
  /* 시리얼 명령: r = 재보정 */
  while (Serial.available()) {
    char c = Serial.read();
    if (c == 'r' || c == 'R')
      startCalibration(F("# 재보정 요청 — 공기 기준선을 다시 수집합니다."));
  }

  updateLed();

  /* 단계 전환 (시간 기반) */
  if (phase == SETTLE && millis() - phaseStart >= SETTLE_MS) {
    phase = BOOTSTRAP;
    phaseStart = millis();
    Serial.println(F("# 공기 기준선 수집 중... (향 넣지 마세요)"));
  } else if (phase == BOOTSTRAP && millis() - phaseStart >= BOOTSTRAP_MS) {
    finalizeBaseline();
    phase = RUN;
    lastDecision = millis();
  }

  /* 측정 (모든 동작 센서) */
  if (millis() - lastMeas >= MEAS_DUR) {
    lastMeas = millis();
    for (uint8_t i = 0; i < N_KIT_SENS; i++) {
      if (!sensorOk[i]) continue;
      if (!bme[i].fetchData()) continue;
      uint8_t nLeft = 0;
      do {
        nLeft = bme[i].getData(d);
        if (!(d.status & BME68X_NEW_DATA_MSK)) continue;
        if (!(d.status & BME68X_GASM_VALID_MSK)) continue;  /* 더미 슬롯 제외 */
        if (d.gas_resistance <= 0) continue;
        if (d.gas_index >= N_STEP) continue;
        handleReading(i, d.gas_index, d.gas_resistance);
      } while (nLeft);
    }
  }

  /* 판정 */
  if (phase == RUN && millis() - lastDecision >= DECISION_MS) {
    lastDecision = millis();
    decide();
  }
}
