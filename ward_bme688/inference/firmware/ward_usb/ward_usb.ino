/**
 * ward_usb.ino
 *
 * 학습 펌웨어 — BME688 DevKit(8센서) 측정값을 노트북 USB 로 CSV 로 흘려보낸다.
 *   tools/capture_serial.py 가 받아 training/recordings/ 에 저장 → 라벨링 → training/data/
 *
 * 히터 설정 (8센서 모두 동일 — ward_usb 와 ward_mqtt 는 반드시 같게)
 *   온도(℃)  {400, 400, 200, 220, 250, 280, 310, 340, 370, 400}
 *   시간배수 {  5,   5,  25,   5,   5,   5,   5,   5,   5,   5}  x 140ms  → 한 사이클 9.8초
 *   - 단계 0~1 400℃ : 이전 사이클 잔류물 태우기
 *   - 단계 2   200℃ : 유일한 하강. 히터는 식는 게 느려서 길게(3.5초) 잡는다
 *   - 단계 3~9      : 220 → 400℃ 상승. 온도마다 반응하는 물질이 달라 서로 다른 정보 7개
 *   100℃ 같은 낮은 온도는 저항이 측정 상한(102.4MΩ)에 붙어 정보가 없어 쓰지 않는다.
 *   ★ 이 값을 바꾸면 그때까지 모은 데이터를 쓸 수 없다 (에러 없이 판정만 망가진다).
 *
 * 사용
 *   1. 업로드 후 Arduino 시리얼 모니터는 닫는다 (포트 충돌 방지).
 *   2. cd tools → python capture_serial.py --port COM5 --name day1
 */

#include "Arduino.h"
#include "bme68xLibrary.h"
#include "commMux.h"

#define N_KIT_SENS 8
#define LED_PIN    LED_BUILTIN
#define MEAS_DUR   140          /* 스텝당 측정 주기(ms) — ward_mqtt 와 같게 */
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
    /* 히터 설정 (모든 센서 동일) — ward_mqtt 와 반드시 같게, 수집 중 변경 금지 */
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
