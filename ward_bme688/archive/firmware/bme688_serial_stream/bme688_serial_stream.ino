/**
 * bme688_serial_stream.ino
 *
 * BME688 DevKit(8센서) 측정값을 시리얼로 실시간 스트리밍한다.
 * PC 에서 predict_serial.py 가 이걸 읽어 실시간 추론을 수행한다.
 * WiFi / MQTT / SD 카드 없이 USB 케이블 하나로 동작한다.
 *
 * ★★★ 가장 중요한 규칙 ★★★
 * 히터 프로파일(tempProf / mulProf / MEAS_DUR)은 학습 때 쓴 로거와
 * 한 글자도 달라서는 안 된다. 다르면 학습된 모델이 통째로 무효가 된다.
 *
 * === 출력 형식 ===
 *   D,센서,스텝,가스저항,온도,습도
 *   예)  D,3,7,182345.6,42.85,23.10
 *
 *   더미 슬롯(Gas Valid=0)은 측정이 아니므로 보내지 않는다.
 *   그 외 안내 문구는 모두 '#' 로 시작하므로 파서가 무시한다.
 *
 * === 사용법 ===
 *   1. 업로드 후 아두이노 시리얼 모니터를 반드시 닫는다 (포트 충돌 방지)
 *   2. PC 에서:  python predict_serial.py --port COM5
 *   3. 발향을 끈 상태로 1~2분 → 기준선 자동 확립
 *   4. 향을 넣으면 화면에 판정이 실시간으로 갱신된다
 */

#include "Arduino.h"
#include "bme68xLibrary.h"
#include "commMux.h"

#define N_KIT_SENS  8
#define LED_PIN     LED_BUILTIN
#define MEAS_DUR    140      /* ★ 로거와 동일해야 함 */
#define BAUD        115200

Bme68x     bme[N_KIT_SENS];
commMux    commSetup[N_KIT_SENS];
bme68xData sensorData[N_KIT_SENS] = {0};

uint32_t lastMeas = 0, lastBlink = 0;
bool     led = false;

void setup(void)
{
  Serial.begin(BAUD);
  pinMode(LED_PIN, OUTPUT);
  commMuxBegin(Wire, SPI);
  delay(200);

  Serial.println();
  Serial.println(F("# BME688 Serial Stream"));

  for (uint8_t i = 0; i < N_KIT_SENS; i++) {
    commSetup[i] = commMuxSetConfig(Wire, SPI, i, commSetup[i]);
    bme[i].begin(BME68X_SPI_INTF, commMuxRead, commMuxWrite, commMuxDelay,
                 &commSetup[i]);
    if (bme[i].checkStatus()) {
      Serial.print(F("# ERROR sensor "));
      Serial.print(i);
      Serial.print(F(": "));
      Serial.println(bme[i].statusString());
      digitalWrite(LED_PIN, HIGH);
      while (1) delay(1000);
    }
  }

  /* --- 히터 프로파일 : 로거와 완전히 동일 (절대 수정 금지) --- */
  for (uint8_t i = 0; i < N_KIT_SENS; i++) {
    bme[i].setTPH();
    uint16_t tempProf[10] = {320, 100, 100, 100, 200, 200, 200, 320, 320, 320};
    uint16_t mulProf[10]  = {5, 2, 10, 30, 5, 5, 5, 5, 5, 5};
    uint16_t sharedHeatrDur =
        MEAS_DUR - (bme[i].getMeasDur(BME68X_PARALLEL_MODE) / INT64_C(1000));
    bme[i].setHeaterProf(tempProf, mulProf, sharedHeatrDur, 10);
    bme[i].setOpMode(BME68X_PARALLEL_MODE);
  }

  Serial.println(F("# sensors ready (heater profile = training profile)"));
  Serial.println(F("# format: D,sensor,step,gas,temp,hum"));
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
    if (!bme[i].fetchData()) continue;

    do {
      nLeft = bme[i].getData(sensorData[i]);
      if (!(sensorData[i].status & BME68X_NEW_DATA_MSK)) continue;

      /* 더미 슬롯은 실제 측정이 아니므로 제외 (전송량 1/8 로 감소) */
      if (!(sensorData[i].status & BME68X_GASM_VALID_MSK)) continue;
      if (sensorData[i].gas_resistance <= 0) continue;

      Serial.print(F("D,"));
      Serial.print(i);                            Serial.print(',');
      Serial.print(sensorData[i].gas_index);      Serial.print(',');
      Serial.print(sensorData[i].gas_resistance, 1); Serial.print(',');
      Serial.print(sensorData[i].temperature, 2); Serial.print(',');
      Serial.println(sensorData[i].humidity, 2);

    } while (nLeft);
  }
}
