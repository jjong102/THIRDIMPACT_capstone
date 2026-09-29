/**
 * bme688_stream_full.ino
 *
 * 학습 데이터 수집용 — BME688 DevKit(8센서)의 모든 측정을 시리얼로 스트리밍한다.
 * SD카드 없이 노트북으로 바로 녹화(capture_serial.py) → 그래프에서 잘라 라벨(label_tool.py).
 *
 * ★ 출력 포맷은 bme688_dev_kit_logger.ino 의 SD CSV 와 "완전히 동일"하다.
 *   그래서 parse_devkit_csv.py 가 그대로 파싱 → train_scent.py 로 이어진다.
 *   (더미 슬롯·품질 플래그까지 그대로 내보내므로 파서의 품질 필터가 정상 동작)
 *
 * ★ 히터 프로파일 = 단일 HP-354 (추론 스케치와 반드시 동일하게 유지).
 *
 * 사용:
 *   1. 업로드 후 아두이노 시리얼 모니터는 닫는다 (포트 충돌 방지).
 *   2. python capture_serial.py --port COM5   → recordings/rec_*.csv 로 녹화
 *   3. python label_tool.py recordings/rec_*.csv --labels Air Citrus Woody Smoke
 */

#include "Arduino.h"
#include "bme68xLibrary.h"
#include "commMux.h"

#define N_KIT_SENS 8
#define LED_PIN    LED_BUILTIN
#define MEAS_DUR   140          /* 스텝당 측정 주기(ms) — 추론과 동일해야 함 */
#define BAUD       115200

Bme68x     bme[N_KIT_SENS];
commMux    commSetup[N_KIT_SENS];
bme68xData d;
bool       sensorOk[N_KIT_SENS] = {false};

uint32_t lastMeas = 0, lastBlink = 0;
bool     led = false;

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
    /* 단일 HP-354 (모든 센서 동일) — 절대 변경 금지 */
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
    Serial.println(F("# ERROR: no working sensor. check wiring."));
    while (1) { digitalWrite(LED_PIN, HIGH); delay(150);
                digitalWrite(LED_PIN, LOW);  delay(150); }
  }
  Serial.print(F("# sensors ready ")); Serial.print(ok); Serial.println(F("/8"));
  Serial.print(F("# heater profile = HP354 (single)  MEAS_DUR="));
  Serial.println(MEAS_DUR);

  /* CSV 헤더 — parse_devkit_csv.py 의 컬럼 별칭과 일치 */
  Serial.println(F("TimeStamp(ms),Sensor Index,Temperature(deg C),Pressure(Pa),"
                   "Humidity(%),Gas Resistance(ohm),Gas Index,Meas Index,IDAC,"
                   "Status,Gas Valid,Heater Stable"));
}

void loop(void)
{
  /* 동작 표시: 1초 점멸 */
  if (millis() - lastBlink >= 1000) {
    lastBlink = millis();
    led = !led;
    digitalWrite(LED_PIN, led);
  }

  if (millis() - lastMeas < MEAS_DUR) return;
  lastMeas = millis();

  uint8_t nLeft = 0;
  for (uint8_t i = 0; i < N_KIT_SENS; i++) {
    if (!sensorOk[i]) continue;
    if (!bme[i].fetchData()) continue;

    do {
      nLeft = bme[i].getData(d);
      if (!(d.status & BME68X_NEW_DATA_MSK)) continue;

      /* 모든 행을 그대로 내보낸다 (더미/불안정 포함) — 필터는 파서가 담당 */
      Serial.print(millis());              Serial.print(',');
      Serial.print(i);                     Serial.print(',');
      Serial.print(d.temperature);         Serial.print(',');
      Serial.print(d.pressure);            Serial.print(',');
      Serial.print(d.humidity);            Serial.print(',');
      Serial.print(d.gas_resistance);      Serial.print(',');
      Serial.print(d.gas_index);           Serial.print(',');
      Serial.print(d.meas_index);          Serial.print(',');
      Serial.print(d.idac);                Serial.print(',');
      Serial.print(d.status, HEX);         Serial.print(',');
      Serial.print(d.status & BME68X_GASM_VALID_MSK); Serial.print(',');
      Serial.println(d.status & BME68X_HEAT_STAB_MSK);

    } while (nLeft);
  }
}
