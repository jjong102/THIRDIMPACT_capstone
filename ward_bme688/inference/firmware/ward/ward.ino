/**
 * ward.ino — 와드 펌웨어 (하나로 수집과 판정을 다 한다)
 *
 *   측정(8센서) ─┬─ USB 시리얼: CSV 전체 행 ──────────────▶ capture_serial.py (USB 수집)
 *                ├─ MQTT diffuser/ward-01/sensor (원시값) ──▶ mqtt_capture.py   (배터리 수집)
 *                └─ ward_infer.h 판정 ── diffuser/ward-01/inference_ward ──▶ ward_view.py · 대시보드
 *   받는 것: diffuser/ward-01/model (새 모델, NVS 저장) · diffuser/ward-01/cmd (REBASE / REBOOT)
 *
 * 설정은 config.h — WARD_WIFI 1(기본, 배터리·MQTT) / 0(USB 전용: WiFi 끔, Brownout 없이 수집).
 *   11/09 까지는 USB 수집용 ward_usb 가 따로 있었다. 출력이 같아 이 펌웨어 하나로 합쳤다.
 *
 * 히터 설정 (8센서 모두 동일) — ★ 이 값을 바꾸면 그때까지 모은 데이터를 쓸 수 없다 (에러 없이 판정만 망가진다).
 *   온도(℃)  {400, 400, 200, 220, 250, 280, 310, 340, 370, 400}
 *   시간배수 {  5,   5,  25,   5,   5,   5,   5,   5,   5,   5}  x 140ms  → 한 사이클 9.8초
 *   - 단계 0~1 400℃ : 이전 사이클 잔류물 태우기
 *   - 단계 2   200℃ : 유일한 하강. 히터는 식는 게 느려서 길게(3.5초) 잡는다
 *   - 단계 3~9      : 220 → 400℃ 상승. 온도마다 반응하는 물질이 달라 서로 다른 정보 7개
 *   100℃ 같은 낮은 온도는 저항이 측정 상한(102.4MΩ)에 붙어 정보가 없어 쓰지 않는다.
 *   tools/parse_devkit_csv.py 의 STEP_TEMPS 와 같아야 한다 — tools/firmware_parity.py 가 자동으로 비교한다.
 *
 * 출력
 *   - USB 시리얼: CSV (더미 포함 전체 행) + '#' 로 시작하는 상태 줄(# I = 판정). 수집 CSV 에는 '#' 줄이 안 섞인다.
 *   - MQTT:       Gas Valid 인 행만 (더미 87% 는 보내지 않음) 약 1초마다 묶어서 보낸다.
 *                 메시지 = CSV 행들 (줄바꿈 구분). 헤더 줄은 없다. 끊겨서 버린 줄이 있으면 "# dropped=N" 줄이 붙는다.
 *                 끊긴 동안은 최근 약 30초 분을 보관했다가 다시 붙으면 보낸다 (그 이전은 버림).
 *   "sensor/air_quality/..." 를 피한 이유: 대시보드의 mqtt_bridge.py 가 sensor/air_quality/# 를 구독하고
 *   "라벨 (NN%)" 형식을 기대한다. 원시 데이터를 거기 보내면 대시보드가 엉뚱한 값을 받는다.
 *
 * LED: 1초 점멸 = MQTT 연결됨 / 빠른 점멸(0.25초) = 아직 연결 안 됨 (WARD_WIFI 0 이면 계속 빠른 점멸)
 *
 * 준비
 *   1. secrets.h.example 을 secrets.h 로 복사해 채움 (WiFi·HiveMQ 자격증명, git 에 안 올라감). 핫스팟은 2.4GHz.
 *   2. 업로드 (배터리 꽂은 채로 돼도, Brownout 재부팅이 반복되면 팬 선을 잠깐 뺀다)
 *   3. cd tools → python ward_view.py (판정 보기) / python mqtt_capture.py (녹화)
 *
 * Brownout: USB + 팬 + WiFi 시작 전류가 겹치면 재부팅이 반복될 수 있다 → WiFi 송신 8.5dBm(config.h).
 */

#include "Arduino.h"
#include "bme68xLibrary.h"
#include "commMux.h"
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include "secrets.h"
#include "config.h"
#if WARD_INFER
#include <Preferences.h>      /* 무선으로 받은 모델을 내부 저장소(NVS)에 보관 */
#include "ward_infer.h"       /* 판정 로직 — PC 도구도 tools/ward_engine.py 로 이 파일을 그대로 쓴다 */
#endif

#define N_KIT_SENS 8
#define LED_PIN    LED_BUILTIN
#define MEAS_DUR   140          /* 스텝당 측정 주기(ms) — 바꾸면 데이터 새로 */
#define BAUD       115200

#define LINE_MAX    96          /* CSV 한 줄 최대 길이 (실제 약 70자) */
#define QUEUE_LEN   240         /* 보관 줄 수. 유효 행 ≈ 8줄/초 → 약 30초 */
#define PAYLOAD_MAX 3000        /* 메시지 1개 최대 바이트 */
#define MQTT_BUF    3400        /* PubSubClient 버퍼 (기본 256 이라 반드시 키워야 함) */

const char    *WIFI_SSID      = SECRET_WIFI_SSID;
const char    *WIFI_PASS      = SECRET_WIFI_PASS;
const char    *MQTT_BROKER    = SECRET_MQTT_BROKER;
const uint16_t MQTT_PORT      = 8883;
const char    *MQTT_USER      = SECRET_MQTT_USER;
const char    *MQTT_PASS      = SECRET_MQTT_PASS;
const char    *MQTT_TOPIC     = TOPIC_SENSOR;          /* config.h: diffuser/ward-01/sensor */
const char    *MQTT_CLIENT_ID = MQTT_CLIENT_ID_STR;    /* config.h: ward-01-raw */

Bme68x     bme[N_KIT_SENS];
commMux    commSetup[N_KIT_SENS];
bme68xData d;
bool       sensorOk[N_KIT_SENS] = {false};

uint32_t lastMeas = 0, lastBlink = 0;
bool     led = false;

WiFiClientSecure espClient;
PubSubClient     mqtt(espClient);

struct Line { char s[LINE_MAX]; };
QueueHandle_t     g_q = NULL;
volatile uint32_t g_dropped = 0;       /* 보관 공간이 차서 버린 줄 수 (누적) */
volatile bool     g_mqtt_ok = false;

/* 측정 루프(core 1) → 네트워킹 태스크(core 0). 가득 차면 가장 오래된 줄을 버린다. 절대 기다리지 않는다. */
static void enqueue(const char *s)
{
  if (g_q == NULL) return;
  Line l;
  strncpy(l.s, s, LINE_MAX);
  l.s[LINE_MAX - 1] = '\0';
  if (xQueueSend(g_q, &l, 0) != pdTRUE) {
    Line junk;
    xQueueReceive(g_q, &junk, 0);
    g_dropped++;
    xQueueSend(g_q, &l, 0);
  }
}

#if WARD_INFER
/* ---------- 온디바이스 판정 ---------- */
#define INFER_MSG_MAX 288
struct InferMsg { char s[INFER_MSG_MAX]; };
QueueHandle_t g_iq = NULL;             /* 판정 JSON → 네트워킹 태스크. 최근 것만 의미 있어 짧게 */
static wi_state_t g_wi;
static uint32_t   g_lastWarmMsg = 0;

/* ---- 무선 모델 교체 ----
 * MQTT 콜백(core 0)은 받은 글자만 복사해 두고, 검사·적용은 측정 루프(core 1)의 model_poll() 에서 한다.
 * 판정 상태(g_wi)를 한 코어에서만 만지기 위해서다. */
static Preferences   g_prefs;
static char          g_model_rx[WI_MODEL_TEXT_MAX + 1];
static volatile size_t g_model_rx_len = 0;
static volatile bool g_model_pending = false;
static const char   *g_model_src = "default";       /* default(model.h) / nvs / mqtt */
#define ACK_MAX 200
struct AckMsg { char s[ACK_MAX]; };
QueueHandle_t g_aq = NULL;                           /* 모델 교체 결과 → 네트워킹 태스크 */

static void model_ack(const char *result, const char *msg)
{
  if (g_aq == NULL) return;
  AckMsg a;
  snprintf(a.s, sizeof(a.s),
           "{\"device_id\":\"%s\",\"result\":\"%s\",\"model\":\"%s\",\"src\":\"%s\",\"msg\":\"%s\",\"uptime_s\":%lu}",
           DEVICE_ID, result, g_wi.M.id, g_model_src, msg, (unsigned long)(millis() / 1000));
  xQueueSend(g_aq, &a, 0);
  Serial.print(F("# model ")); Serial.print(result); Serial.print(F(" id=")); Serial.print(g_wi.M.id);
  Serial.print(F(" src=")); Serial.print(g_model_src); Serial.print(' '); Serial.println(msg);
}

/* core 0: PubSubClient 콜백 — 복사만 */
static volatile int g_cmd = 0;                        /* 1 = REBASE, 2 = REBOOT (TOPIC_CMD) */

static void on_mqtt(char *topic, byte *payload, unsigned int len)
{
  if (strcmp(topic, TOPIC_CMD) == 0) {
    if (len >= 6 && memcmp(payload, "REBASE", 6) == 0) g_cmd = 1;
    else if (len >= 6 && memcmp(payload, "REBOOT", 6) == 0) g_cmd = 2;
    return;
  }
  if (strcmp(topic, TOPIC_MODEL) != 0) return;
  if (len == 0 || g_model_pending) return;           /* 빈 메시지 = retained 지움 / 처리 중이면 다음에 */
  if (len > WI_MODEL_TEXT_MAX) len = WI_MODEL_TEXT_MAX + 1;   /* 너무 길면 검사에서 size 로 거절 */
  size_t n = len > WI_MODEL_TEXT_MAX ? WI_MODEL_TEXT_MAX : len;
  memcpy(g_model_rx, payload, n);
  g_model_rx[n] = '\0';
  g_model_rx_len = len;
  g_model_pending = true;
}

static void model_apply(const wi_model_t &M, const char *src)
{
  g_wi.M = M;
  g_wi.pred_n = 0; g_wi.pred_flag = 0;               /* 이전 모델로 낸 이번 라운드 판정은 버림 */
  g_model_src = src;
}

/* core 1: 받은 모델 검사 → 적용 → 저장 → 답장 */
static void model_poll()
{
  if (g_cmd == 1) {                                  /* 기준선만 다시: 모델은 그대로 두고 판정 상태 초기화 */
    g_cmd = 0;
    wi_model_t M = g_wi.M;
    wi_init(&g_wi, g_wi.P);
    g_wi.M = M;
    g_lastWarmMsg = 0;
    model_ack("rebase", "기준선 다시 잡는 중 (1분 평평하면)");
  } else if (g_cmd == 2) {
    g_cmd = 0;
    if (millis() > 30000) {                           /* 부팅 직후엔 무시 (혹시 남은 명령으로 재부팅 반복 방지) */
      model_ack("reboot", "재시작");
      delay(1500);                                    /* 답장이 나갈 시간 */
      ESP.restart();
    }
  }
  if (!g_model_pending) return;
  size_t len = g_model_rx_len;
  if (len >= 15 && strncmp(g_model_rx, "WARDMODEL RESET", 15) == 0) {
    g_prefs.remove("model");
    model_apply(WI_DEFAULT_MODEL, "default");
    model_ack("reset", "기본 모델로 돌아감");
  } else {
    wi_model_t M; char err[40];
    if (!wi_model_parse(g_model_rx, len, &M, err, sizeof(err))) {
      model_ack("error", err);                       /* 지금 모델은 그대로 */
    } else if (strcmp(M.id, g_wi.M.id) == 0) {
      model_ack("same", "이미 쓰는 모델");
    } else {
      model_apply(M, "mqtt");
      size_t w = g_prefs.putBytes("model", g_model_rx, len);
      model_ack(w == len ? "ok" : "ok_nosave", w == len ? "적용·저장" : "적용했지만 저장 실패");
    }
  }
  g_model_pending = false;
}

/* 부팅: 저장된 모델이 있고 검사를 통과하면 그것을, 아니면 펌웨어 기본 모델 */
static void model_boot()
{
  g_prefs.begin("ward", false);
  size_t len = g_prefs.getBytesLength("model");
  if (len > 0 && len <= WI_MODEL_TEXT_MAX) {
    g_prefs.getBytes("model", g_model_rx, len);
    wi_model_t M; char err[40];
    if (wi_model_parse(g_model_rx, len, &M, err, sizeof(err))) { model_apply(M, "nvs"); return; }
    Serial.print(F("# 저장된 모델이 깨짐 (")); Serial.print(err); Serial.println(F(") → 기본 모델"));
    g_prefs.remove("model");
  }
  model_apply(WI_DEFAULT_MODEL, "default");
}

/* 상태 이름 (대시보드용): 기준선 전 WARMUP / Air CLEAN / 우리 향 SCENT / 악취 MALODOR / Unknown / 보류 HOLD */
static const char *infer_state(int top)
{
  if (top == WI_AIR) return "CLEAN";
  if (top == WI_WEAK || top == WI_NONE) return "HOLD";
  if (top == WI_UNKNOWN) return "UNKNOWN";
  return g_wi.M.c[top].on ? "MALODOR" : "SCENT";
}

static void infer_push(const char *json)
{
  if (g_iq == NULL) return;
  InferMsg m;
  strncpy(m.s, json, INFER_MSG_MAX);
  m.s[INFER_MSG_MAX - 1] = '\0';
  if (xQueueSend(g_iq, &m, 0) != pdTRUE) {          /* 가득 차면 가장 오래된 것을 버림 */
    InferMsg junk;
    xQueueReceive(g_iq, &junk, 0);
    xQueueSend(g_iq, &m, 0);
  }
}

static void infer_publish_decision(const wi_decision_t &D)
{
  char js[INFER_MSG_MAX];
  const char *st = infer_state(D.top);
  bool scent = (D.top >= 0 && !g_wi.M.c[D.top].on);
  snprintf(js, sizeof(js),
           "{\"device_id\":\"%s\",\"src\":\"ward\",\"model\":\"%s\",\"uptime_s\":%lu,"
           "\"state\":\"%s\",\"label\":\"%s\",\"label_note\":\"%s\",\"mag\":%.3f,"
           "\"votes\":%d,\"n\":%d,\"purifier\":\"%s\",\"event\":\"%s\","
           "\"alert\":{\"malodor\":%s},\"baseline_age_s\":%.0f}",
           DEVICE_ID, g_wi.M.id, (unsigned long)(millis() / 1000),
           st, wi_label_name(&g_wi, D.top), scent ? "추정" : "", isfinite(D.mag) ? D.mag : -1.0f,
           D.k, D.n, D.purifier ? "on" : "off",
           D.event == 1 ? "ON" : (D.event == 2 ? "OFF" : ""),
           (D.purifier && (D.top == WI_UNKNOWN || (D.top >= 0 && g_wi.M.c[D.top].on))) ? "true" : "false",
           g_wi.sec_now - g_wi.base_sec);
  infer_push(js);
  /* 시리얼에도 한 줄 ('#' 로 시작해서 capture_serial.py 의 CSV 에는 안 섞임) */
  Serial.print(F("# I ")); Serial.print(D.sec, 1); Serial.print(' ');
  Serial.print(st); Serial.print(' '); Serial.print(wi_label_name(&g_wi, D.top));
  Serial.print(' '); Serial.print(D.k); Serial.print('/'); Serial.print(D.n);
  Serial.print(F(" mag=")); Serial.print(D.mag, 2);
  Serial.print(F(" purifier=")); Serial.print(D.purifier ? "ON" : "off");
  if (D.event) Serial.print(D.event == 1 ? F("  << ON") : F("  << OFF"));
  if (D.rebased) Serial.print(D.rebased == 2 ? F("  (켜진 채 변화 없음 → 기준선 다시 잡음)") : F("  (기준선 다시 잡음)"));
  Serial.println();
}

static void infer_warmup_tick()
{
  if (g_wi.has_base || millis() - g_lastWarmMsg < 10000) return;
  g_lastWarmMsg = millis();
  char js[160];
  snprintf(js, sizeof(js),
           "{\"device_id\":\"%s\",\"src\":\"ward\",\"model\":\"%s\",\"uptime_s\":%lu,\"state\":\"WARMUP\"}",
           DEVICE_ID, g_wi.M.id, (unsigned long)(millis() / 1000));
  infer_push(js);
}
#endif

/* ---------- 네트워킹 (core 0 전용 태스크) ---------- */
static void wifi_connect()
{
  if (WiFi.status() == WL_CONNECTED) return;
  WiFi.mode(WIFI_STA);
  /* 송신 출력을 낮춘다: 400℃ 히터 8개 + WiFi 송신 순간 전류가 겹치면 전압이 떨어져
     Brownout 리셋이 반복됐다(11/04, USB 전원). 핫스팟이 가까우면 8.5dBm 으로 충분하다. */
  WiFi.setTxPower(WIFI_TX_POWER);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
}

static void mqtt_connect()
{
  if (mqtt.connected()) return;
  if (WiFi.status() != WL_CONNECTED) return;
  espClient.setInsecure();               /* 서버 인증서 검증 생략 (데모 펌웨어와 동일) */
  espClient.setTimeout(5);
  mqtt.setServer(MQTT_BROKER, MQTT_PORT);
  mqtt.setKeepAlive(60);
  mqtt.setSocketTimeout(5);
  mqtt.connect(MQTT_CLIENT_ID, MQTT_USER, MQTT_PASS);
}

static void mqtt_task(void *arg)
{
  (void)arg;
  static char payload[PAYLOAD_MAX + LINE_MAX + 64];
  bool wasConnected = false;
  uint32_t reportedDrop = 0;
  for (;;) {
    bool now = mqtt.connected();
    g_mqtt_ok = now;
    if (now && !wasConnected) {
      Serial.print(F("# MQTT 연결됨 → 발행 시작 (topic="));
      Serial.print(MQTT_TOPIC); Serial.println(F(")"));
#if WARD_INFER
      mqtt.subscribe(TOPIC_MODEL, 1);                /* retained 로 남아 있는 최신 모델이 바로 온다 */
      mqtt.subscribe(TOPIC_CMD, 0);
#endif
    } else if (!now && wasConnected) {
      Serial.println(F("# MQTT 끊김 → 재접속 시도"));
    }
    wasConnected = now;

    if (WiFi.status() != WL_CONNECTED)      wifi_connect();
    else if (!now)                          mqtt_connect();
    else {
      mqtt.loop();
      /* 밀린 게 있으면 한 번에 최대 4개 메시지까지 보내 따라잡는다 */
      for (int b = 0; b < 4 && uxQueueMessagesWaiting(g_q) > 0; b++) {
        size_t n = 0;
        uint32_t dr = g_dropped;
        if (dr != reportedDrop) {
          n += snprintf(payload + n, sizeof(payload) - n, "# dropped=%lu\n", (unsigned long)dr);
          reportedDrop = dr;
        }
        Line l;
        while (n < PAYLOAD_MAX && xQueueReceive(g_q, &l, 0) == pdTRUE) {
          size_t k = strlen(l.s);
          memcpy(payload + n, l.s, k);
          n += k;
          payload[n++] = '\n';
        }
        payload[n] = '\0';
        if (!mqtt.publish(MQTT_TOPIC, (const uint8_t *)payload, n, false)) {
          Serial.println(F("# MQTT 발행 실패 (이 묶음은 버림)"));
          break;
        }
      }
#if WARD_INFER
      InferMsg m;
      while (g_iq != NULL && xQueueReceive(g_iq, &m, 0) == pdTRUE)
        mqtt.publish(TOPIC_INFER, m.s, false);
      AckMsg a;
      while (g_aq != NULL && xQueueReceive(g_aq, &a, 0) == pdTRUE)
        mqtt.publish(TOPIC_MODEL_ACK, a.s, false);
#endif
    }
    vTaskDelay(pdMS_TO_TICKS(1000));
  }
}

/* ---------- 측정 ---------- */
void setup(void)
{
  Serial.begin(BAUD);
  pinMode(LED_PIN, OUTPUT);
  commMuxBegin(Wire, SPI);
  delay(200);

  uint8_t ok = 0;
  for (uint8_t i = 0; i < N_KIT_SENS; i++) {
    commSetup[i] = commMuxSetConfig(Wire, SPI, i, commSetup[i]);
    bme[i].begin(BME68X_SPI_INTF, commMuxRead, commMuxWrite, commMuxDelay,
                 &commSetup[i]);
    if (bme[i].checkStatus()) {
      Serial.print(F("# WARN sensor ")); Serial.print(i);
      Serial.print(F(" init fail: ")); Serial.println(bme[i].statusString());
      sensorOk[i] = false;
      continue;
    }
    /* 히터 설정 (모든 센서 동일) — 맨 위 설명 참고, 바꾸면 데이터 새로 */
    bme[i].setTPH();
    uint16_t tempProf[10] = {400, 400, 200, 220, 250, 280, 310, 340, 370, 400};
    uint16_t mulProf[10]  = {  5,   5,  25,   5,   5,   5,   5,   5,   5,   5};
    uint16_t sharedHeatrDur =
        MEAS_DUR - (bme[i].getMeasDur(BME68X_PARALLEL_MODE) / INT64_C(1000));
    bme[i].setHeaterProf(tempProf, mulProf, sharedHeatrDur, 10);
    bme[i].setOpMode(BME68X_PARALLEL_MODE);
    sensorOk[i] = true;
    ok++;
  }

  if (ok == 0) {
    Serial.println(F("# ERROR: no working sensor. check wiring."));
    while (1) { digitalWrite(LED_PIN, HIGH); delay(150);
                digitalWrite(LED_PIN, LOW);  delay(150); }
  }
  Serial.print(F("# sensors ready ")); Serial.print(ok); Serial.println(F("/8"));
  Serial.print(F("# heater {400,400,200,220,250,280,310,340,370,400}"
                 " mul{5,5,25,5,5,5,5,5,5,5} cycle=9.8s  MEAS_DUR="));
  Serial.println(MEAS_DUR);
  Serial.print(F("# MQTT topic=")); Serial.println(MQTT_TOPIC);
  Serial.println(F("TimeStamp(ms),Sensor Index,Temperature(deg C),Pressure(Pa),"
                   "Humidity(%),Gas Resistance(ohm),Gas Index,Meas Index,IDAC,"
                   "Status,Gas Valid,Heater Stable"));

  g_q = xQueueCreate(QUEUE_LEN, sizeof(Line));
  if (g_q == NULL) Serial.println(F("# WARN: 큐 생성 실패 → MQTT 전송 안 함"));
#if WARD_INFER
  wi_init(&g_wi, wi_default_params());
  model_boot();                                      /* 저장된 무선 모델이 있으면 그것 */
  g_iq = xQueueCreate(6, sizeof(InferMsg));
  g_aq = xQueueCreate(2, sizeof(AckMsg));
  Serial.print(F("# infer on  model=")); Serial.print(g_wi.M.id);
  Serial.print(F(" (")); Serial.print(g_model_src); Serial.print(F(")  classes="));
  for (int c = 0; c < g_wi.M.n; c++) {
    Serial.print(g_wi.M.c[c].name); Serial.print(g_wi.M.c[c].on ? "(on) " : " ");
  }
  Serial.print(F(" topic=")); Serial.print(TOPIC_INFER);
  Serial.print(F("  model topic=")); Serial.println(TOPIC_MODEL);
  mqtt.setCallback(on_mqtt);
#endif
  mqtt.setBufferSize(MQTT_BUF);
#if WARD_WIFI
  /* 네트워킹은 core 0 전용 태스크. 측정 루프(core 1)를 막지 않는다 */
  xTaskCreatePinnedToCore(mqtt_task, "mqtt", 16384, NULL, 1, NULL, 0);
#else
  WiFi.mode(WIFI_OFF);
  Serial.println(F("# WiFi 꺼짐 (WARD_WIFI 0) — 판정은 시리얼 '# I' 줄로만"));
#endif
}

void loop(void)
{
  /* LED: 연결되면 1초 점멸, 아니면 빠른 점멸 */
  uint32_t blinkMs = g_mqtt_ok ? 1000 : 250;
  if (millis() - lastBlink >= blinkMs) {
    lastBlink = millis();
    led = !led;
    digitalWrite(LED_PIN, led);
  }

  if (millis() - lastMeas < MEAS_DUR) return;
  lastMeas = millis();

  uint8_t nLeft = 0;
  char line[LINE_MAX];
  for (uint8_t i = 0; i < N_KIT_SENS; i++) {
    if (!sensorOk[i]) continue;
    if (!bme[i].fetchData()) continue;

    do {
      nLeft = bme[i].getData(d);
      if (!(d.status & BME68X_NEW_DATA_MSK)) continue;

      /* 수집 CSV 형식 (예전 ward_usb 의 Serial.print(float) 와 같게 소수 둘째 자리) */
      snprintf(line, sizeof(line), "%lu,%u,%.2f,%.2f,%.2f,%.2f,%u,%u,%u,%X,%u,%u",
               (unsigned long)millis(), (unsigned)i,
               d.temperature, d.pressure, d.humidity, d.gas_resistance,
               (unsigned)d.gas_index, (unsigned)d.meas_index, (unsigned)d.idac,
               (unsigned)d.status,
               (unsigned)(d.status & BME68X_GASM_VALID_MSK),
               (unsigned)(d.status & BME68X_HEAT_STAB_MSK));
      Serial.println(line);
      if (d.status & BME68X_GASM_VALID_MSK) enqueue(line);   /* 더미는 보내지 않음 */
#if WARD_INFER
      if (wi_feed(&g_wi, (double)millis(), i, d.gas_resistance, d.gas_index,
                  (d.status & BME68X_GASM_VALID_MSK) ? 1 : 0))
        infer_publish_decision(g_wi.last);
#endif

    } while (nLeft);
  }
#if WARD_INFER
  infer_warmup_tick();
  model_poll();
#endif
}
