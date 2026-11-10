/**
 * bme688_bsec_infer.ino
 *
 * BME AI-Studio 로 학습한 4클래스 알고리즘을 BSEC 로 실행해
 * 시리얼에 실시간 판정을 출력한다.
 *
 * === 클래스 매핑 (aiconfig 기준, 절대 바꾸지 말 것) ===
 *   GAS_ESTIMATE_1 = Air
 *   GAS_ESTIMATE_2 = Citrus
 *   GAS_ESTIMATE_3 = Floral
 *   GAS_ESTIMATE_4 = Woody
 *
 * === 준비물 ===
 *   0. commMux.h / commMux.cpp 를 이 폴더에 복사한다.
 *      두 가지 판본이 돌아다니므로 이름 규칙을 확인할 것:
 *        BME68x_Sensor_library 판 : commMuxBegin, commMuxSetConfig ... (카멜케이스) ← 이 스케치가 쓰는 쪽
 *        bsec2 라이브러리 판       : comm_mux_begin, comm_mux_set_config ... (스네이크케이스)
 *      스네이크케이스 판을 쓰고 있다면 이 스케치의 함수명을 그쪽으로 바꾸면 된다.
 *
 *   1. AI-Studio 에서 내보낸 두 파일을 이 스케치 폴더에 넣는다.
 *      aircitrusfloralw_354_10.c → bsec_serialized_configurations_selectivity.c
 *      aircitrusfloralw_354_10.h → bsec_serialized_configurations_selectivity.h
 *      (.c 파일이 이 이름의 헤더를 include 하고 있으므로 이름을 맞춰야 한다)
 *
 *   2. bsec2 라이브러리가 'selectivity(IAQ_Sel)' 알고리즘 바이너리를 써야 한다.
 *      기본 배포판은 IAQ 전용이라 4클래스 분류가 동작하지 않는다.
 *      예전에 2클래스로 성공했을 때 쓴 그 라이브러리 그대로 사용할 것.
 *
 *   3. BSEC 버전이 2.6.1.0 이어야 한다 (aiconfig 의 bsecVersion).
 *      버전이 다르면 setConfig 가 -34 를 돌려준다.
 *
 * === 화면 ===
 *   센서별 결과가 모이면 8센서 평균 확률과 최종 판정을 한 줄로 출력한다.
 *
 *     [S6] Air 0.02  Citrus 0.91  Floral 0.05  Woody 0.02
 *     >>> CITRUS   (8센서 평균, 최대 0.88)
 */

#include <Arduino.h>
#include <bsec2.h>
#include "commMux.h"
#include "bsec_serialized_configurations_selectivity.h"

/* 예전에 스택 오버플로를 겪은 적이 있어 루프 태스크 스택을 키워 둔다 */
#if defined(ARDUINO_ARCH_ESP32)
SET_LOOP_TASK_STACK_SIZE(16 * 1024);
#endif

#define N_SENS        8
#define LED_PIN       LED_BUILTIN
#define N_CLASS       4

/* 라이브러리에 따라 ARRAY_LEN 이 없을 수 있으므로 직접 정의 */
#ifndef ARRAY_LEN
#define ARRAY_LEN(x)  (sizeof(x) / sizeof((x)[0]))
#endif

/* aiconfig 의 classes 순서와 반드시 같아야 한다 */
const char* CLASS_NAME[N_CLASS] = {"Air", "Citrus", "Floral", "Woody"};

Bsec2     envSensor[N_SENS];
commMux   commConf[N_SENS];
uint8_t   bsecMemBlock[N_SENS][BSEC_INSTANCE_SIZE];

/* 센서별 최근 확률과 도착 여부 */
float probs[N_SENS][N_CLASS];
bool  hasResult[N_SENS];
uint8_t curSensor = 0;

void checkBsecStatus(Bsec2 bsec, uint8_t idx, const char* where);
void newDataCallback(const bme68x_data data, const bsecOutputs outputs, Bsec2 bsec);
void report(void);

void setup(void)
{
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  delay(500);

  Serial.println();
  Serial.println(F("=== BME688 BSEC 4-class inference ==="));
  Serial.print(F("config size: "));
  Serial.println(sizeof(bsec_config_selectivity));

  commMuxBegin(Wire, SPI);

  /* 구독 목록은 GAS_ESTIMATE 4개만.
   * RAW_GAS / STABILIZATION_STATUS / RUN_IN_STATUS 를 함께 넣으면
   * 이들이 가스 스캔 모드(BSEC_SAMPLE_RATE_SCAN)를 지원하지 않아
   * 구독 전체가 -12 (BSEC_E_SU_SAMPLERATELIMITS) 로 거부된다. */
  bsecSensor sensorList[] = {
    BSEC_OUTPUT_GAS_ESTIMATE_1,
    BSEC_OUTPUT_GAS_ESTIMATE_2,
    BSEC_OUTPUT_GAS_ESTIMATE_3,
    BSEC_OUTPUT_GAS_ESTIMATE_4,
  };

  for (uint8_t i = 0; i < N_SENS; i++) {
    commConf[i] = commMuxSetConfig(Wire, SPI, i, commConf[i]);

    /* 인스턴스마다 메모리 블록을 직접 지정해야 8개를 동시에 돌릴 수 있다 */
    envSensor[i].allocateMemory(bsecMemBlock[i]);

    if (!envSensor[i].begin(BME68X_SPI_INTF, commMuxRead, commMuxWrite,
                            commMuxDelay, &commConf[i])) {
      checkBsecStatus(envSensor[i], i, "begin");
      continue;
    }

    /* AI-Studio 설정 주입. 여기서 -34 가 나오면 BSEC 버전 불일치 */
    if (!envSensor[i].setConfig(bsec_config_selectivity)) {
      checkBsecStatus(envSensor[i], i, "setConfig");
      continue;
    }

    /* 가스 스캔 모드로 구독. 학습한 히터 프로파일이 이 설정에 들어 있다 */
    if (!envSensor[i].updateSubscription(sensorList,
                                         ARRAY_LEN(sensorList),
                                         BSEC_SAMPLE_RATE_SCAN)) {
      checkBsecStatus(envSensor[i], i, "updateSubscription");
      continue;
    }

    envSensor[i].attachCallback(newDataCallback);
    Serial.println("sensor " + String(i) + " ready");
  }

  Serial.println(F("--- 판정 시작 (한 사이클 약 11초) ---"));
}

void loop(void)
{
  digitalWrite(LED_PIN, (millis() / 1000) % 2);

  for (curSensor = 0; curSensor < N_SENS; curSensor++) {
    if (!envSensor[curSensor].run()) {
      checkBsecStatus(envSensor[curSensor], curSensor, "run");
    }
  }
}

/*!
 * @brief 새 출력이 나오면 호출된다. 센서 번호는 curSensor 로 알 수 있다.
 */
void newDataCallback(const bme68x_data data, const bsecOutputs outputs, Bsec2 bsec)
{
  if (!outputs.nOutputs) return;

  uint8_t s = curSensor;
  bool got = false;

  for (uint8_t i = 0; i < outputs.nOutputs; i++) {
    const bsec_output_t& o = outputs.output[i];
    switch (o.sensor_id) {
      case BSEC_OUTPUT_GAS_ESTIMATE_1: probs[s][0] = o.signal; got = true; break;
      case BSEC_OUTPUT_GAS_ESTIMATE_2: probs[s][1] = o.signal; got = true; break;
      case BSEC_OUTPUT_GAS_ESTIMATE_3: probs[s][2] = o.signal; got = true; break;
      case BSEC_OUTPUT_GAS_ESTIMATE_4: probs[s][3] = o.signal; got = true; break;
      default: break;
    }
  }
  if (!got) return;

  hasResult[s] = true;

  Serial.print("[S"); Serial.print(s); Serial.print("] ");
  for (uint8_t c = 0; c < N_CLASS; c++) {
    Serial.print(CLASS_NAME[c]); Serial.print(' ');
    Serial.print(probs[s][c], 2); Serial.print("  ");
  }
  Serial.println();

  /* 8센서가 모두 모이면 평균 내어 최종 판정 */
  for (uint8_t i = 0; i < N_SENS; i++) if (!hasResult[i]) return;
  report();
}

/*!
 * @brief 8센서 확률을 평균해 최종 라벨을 출력한다.
 */
void report(void)
{
  float m[N_CLASS] = {0};
  for (uint8_t c = 0; c < N_CLASS; c++) {
    for (uint8_t s = 0; s < N_SENS; s++) m[c] += probs[s][c];
    m[c] /= N_SENS;
  }

  uint8_t best = 0;
  for (uint8_t c = 1; c < N_CLASS; c++) if (m[c] > m[best]) best = c;

  Serial.print(F(">>> "));
  Serial.print(CLASS_NAME[best]);
  Serial.print(F("   (8센서 평균, 최대 "));
  Serial.print(m[best], 2);
  Serial.println(F(")"));
  Serial.println();

  for (uint8_t s = 0; s < N_SENS; s++) hasResult[s] = false;
}

/*!
 * @brief BSEC / 센서 오류를 사람이 읽을 수 있게 출력한다.
 *        자주 만나는 코드:
 *          -34 : 설정 파일과 BSEC 라이브러리 버전 불일치
 *          -12 : 구독한 출력이 이 알고리즘에 없음 (IAQ 전용 라이브러리일 때)
 */
void checkBsecStatus(Bsec2 bsec, uint8_t idx, const char* where)
{
  if (bsec.status < BSEC_OK) {
    Serial.print(F("[BSEC error] sensor ")); Serial.print(idx);
    Serial.print(F(" at ")); Serial.print(where);
    Serial.print(F("  code ")); Serial.println(bsec.status);

    switch (bsec.status) {
      case -12:
        Serial.println(F("  → 구독한 출력 중 가스 스캔 모드를 지원하지 않는 것이 있음"));
        break;
      case -14:
        Serial.println(F("  → 샘플레이트가 이 알고리즘과 맞지 않음"));
        break;
      case -32:
      case -34:
        Serial.println(F("  → 설정과 BSEC 라이브러리 버전 불일치. 2.6.1.0 확인"));
        break;
      default:
        break;
    }
  } else if (bsec.status > BSEC_OK) {
    Serial.print(F("[BSEC warn] sensor ")); Serial.print(idx);
    Serial.print(F(" at ")); Serial.print(where);
    Serial.print(F("  code ")); Serial.println(bsec.status);
  }

  if (bsec.sensor.status < BME68X_OK) {
    Serial.print(F("[BME68X error] sensor ")); Serial.print(idx);
    Serial.print(F(" code ")); Serial.println(bsec.sensor.status);
  } else if (bsec.sensor.status > BME68X_OK) {
    Serial.print(F("[BME68X warn] sensor ")); Serial.print(idx);
    Serial.print(F(" code ")); Serial.println(bsec.sensor.status);
  }
}
