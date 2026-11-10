/**
 * bme688_dev_kit_logger.ino
 *
 * Bosch bme688_dev_kit.ino 를 다중 세션 데이터 수집용으로 수정한 버전.
 * 원본: Copyright (C) 2021 Bosch Sensortec GmbH, SPDX BSD-3-Clause
 *
 * === 원본에서 바꾼 것 ===
 *
 * [1] 파일명 자동 증가
 *     원본: LOG_FILE_NAME 고정 + setup()에서 sd.remove() → 전원 켤 때마다 이전 녹음 삭제
 *     수정: /LOG_0001.csv, /LOG_0002.csv ... 빈 번호를 찾아 새로 만듦. 기존 파일 보존
 *
 * [2] 무한루프 제거
 *     원본: 데이터 인덱스를 하나라도 놓치면 panicLeds() = while(1) 무한정지
 *     수정: 놓친 개수만 세고 계속 진행 (초기화 실패처럼 치명적인 경우만 정지)
 *
 * [3] 세션 진행 상황 표시
 *     10초마다 [경과시간 / 사이클수 / 놓친수 / 보드온도] 한 줄 요약
 *
 * [4] 시리얼 명령으로 세션 전환 (전원 유지)
 *     전원을 껐다 켜면 보드가 식어 매번 예열을 반복해야 하고 세션마다
 *     열적 상태가 달라진다. 'n' 으로 파일만 전환하면 예열은 처음 한 번.
 *
 * [5] 지연 기록 버퍼 (오염 구간 자동 폐기)
 *     데이터를 곧바로 SD에 쓰지 않고 10초 동안 RAM에 보류했다가,
 *     10초가 지난 것만 기록. 'p'/'n' 을 치면 보류분(=최근 10초)은 통째로 폐기.
 *     → 사람이 접근하며 생긴 오염이 파일에 남지 않는다.
 *
 * [6] 세션 전환/재개 직후 놓침 오카운트 방지
 *     전환/재개 후 RESYNC_MS 동안은 인덱스를 추종만 하고 세지 않는다.
 *     SD 쓰기 실패는 별도 카운터(writeErrors)로 분리.
 *
 * [7] 보드 버튼 조작 + LED 상태 표시 (노트북 불필요)
 *       SW_START (GPIO 32) : 녹화 시작 / 일시정지 토글
 *       SW_NEXT  (GPIO 14) : 짧게 누름 = 블록 종료+저장 / 길게 2초 = 모드 전환
 *
 *     LED 신호:
 *       느린 점멸 (1초)   = 정지 중. 향 세팅하고 SW_START
 *       빠른 점멸 (0.1초) = 시작 카운트다운. 손 떼고 물러날 시간
 *       가끔 짧게 반짝    = 녹화 중
 *       길게 3번          = 블록 완료 (자동 정지)
 *       짧게 2번          = 수동 모드로 전환됨
 *       짧게 4번          = 자동 모드로 전환됨
 *
 * [8] 수동 / 자동 블록 모드  ← 이번에 추가
 *     문제: 자동 종료(2~3분)는 짧은 블록 교대 수집에는 편하지만,
 *           안정화 측정처럼 한 시간짜리 연속 로그를 남길 수 없다.
 *     수정: 두 모드를 실행 중에 전환할 수 있게 했다.
 *
 *       수동 모드 (기본) : 시간 제한 없음. SW_NEXT 를 누를 때까지 계속 기록
 *       자동 모드        : AUTO_BLOCK_MS 경과 시 알아서 저장하고 정지
 *
 *     전환 방법 (둘 다 가능):
 *       - 시리얼에 'a'
 *       - SW_NEXT 를 2초 이상 길게 누름 (노트북 없이 현장에서)
 *
 *     시작 모드는 START_IN_MANUAL 로 정한다 (1=수동, 0=자동).
 *
 * === 사용법 A: 긴 연속 로그 (안정화 측정용) ===
 *   1. START_IN_MANUAL 을 1 로 두고 업로드
 *   2. 전원 ON → SW_NEXT 한 번 (첫 파일 정리) → 정지 상태
 *   3. SW_START → 15초 후 녹화 시작 → 그대로 방치 (몇 시간이든 계속 기록)
 *   4. 끝낼 때 SW_NEXT 한 번 → 저장 후 정지
 *
 * === 사용법 B: 짧은 블록 교대 수집 ===
 *   1. SW_NEXT 를 2초 길게 눌러 자동 모드로 전환 (LED 4번 반짝)
 *   2. 향 세팅 → SW_START → 물러남 → AUTO_BLOCK_MS 뒤 자동 저장+정지
 *   3. 향 교체/환기 → 2번 반복
 *
 * [9] 시리얼로 라벨 입력 → 파일명에 자동으로 붙음
 *     문제: LOG_0031.csv 같은 번호만으로는 나중에 어느 향인지 헷갈린다.
 *           실제로 Woody/Citrus 를 뒤바꿔 정리해 결과를 잘못 읽은 적이 있다.
 *     수정: 시리얼에 향 이름을 치면 그 블록의 라벨이 되고,
 *           블록이 끝날 때 파일명이 자동으로 바뀐다.
 *
 *           woody  입력 → LOG_0031.csv 가 LOG_0031_Woody.csv 로 저장됨
 *
 *     - 라벨은 블록 시작 전/중 아무 때나 입력해도 된다 (끝날 때 반영)
 *     - 한 블록이 끝나면 라벨은 지워진다. 매 블록 다시 입력할 것
 *     - 아무 단어나 쓸 수 있다 (mint, coffee 등). 영문/숫자만 남고 첫 글자는 대문자
 *     - 시리얼 모니터의 줄바꿈 설정과 무관하게 동작한다
 *       (줄바꿈이 없으면 입력이 멈춘 뒤 0.15초에 자동 처리)
 *
 * === 조작 요약 ===
 *   SW_START | 'p' : 시작 / 일시정지
 *   SW_NEXT(짧게) | 'n' : 블록 종료 → 저장 + 정지
 *   SW_NEXT(2초)  | 'a' : 수동 ↔ 자동 모드 전환
 *                  's' : 현재 상태 출력 (시리얼 전용)
 *   향 이름 입력       : 이번 블록의 라벨 지정 (예: air / citrus / floral / woody)
 */

#include "Arduino.h"
#include "bme68xLibrary.h"
#include "commMux.h"

#include <SdFat.h>
#include <Esp.h>


#define N_KIT_SENS      8
#define SD_PIN_CS       33
#define PANIC_LED       LED_BUILTIN
#define PANIC_DUR       1000
#define MEAS_DUR        140       /* 측정 주기 (ms) */
#define STATUS_INTERVAL 10000     /* 시리얼 요약 출력 간격 (ms) */
#define MAX_LOG_FILES   9999
#define DISCARD_WINDOW_MS 10000   /* [5] 최근 N ms 는 RAM에 보류, 명령 시 폐기 */
#define N_CHUNKS        90        /* 보류 버퍼 슬롯 수 (10s / 140ms ≈ 72 + 여유) */
#define RESYNC_MS       3000      /* [6] 전환/재개 후 놓침 판정 유예 시간 */

/* ---- [7] 버튼 설정 (Bosch label_provider.h 의 PIN_BUTTON_1/2 와 동일) ---- */
#define BTN_START       32     /* 시작/일시정지 토글 (보드 버튼 1) */
#define BTN_NEXT        14     /* 블록 종료 / 길게=모드 전환 (보드 버튼 2) */
#define BTN_ACTIVE_LOW  1
#define DEBOUNCE_MS     50
#define LONGPRESS_MS    2000   /* [8] 이 시간 이상 누르면 모드 전환 */

/* ---- [8] 블록 모드 ---- */
#define START_IN_MANUAL 1         /* 1 = 수동(시간제한 없음), 0 = 자동 */
#define AUTO_BLOCK_MS   180000UL  /* 자동 모드일 때의 블록 길이 (3분) */

#define START_DELAY_MS  5000UL   /* 시작 전 대기 (손 떼고 물러날 시간) */

Bme68x     bme[N_KIT_SENS];
commMux    commSetup[N_KIT_SENS];
uint8_t    lastMeasindex[N_KIT_SENS] = {0};
bme68xData sensorData[N_KIT_SENS] = {0};

/* [5] 지연 기록 링버퍼 */
String   chunkData[N_CHUNKS];
uint32_t chunkTime[N_CHUNKS];
uint8_t  chunkHead = 0;
uint8_t  chunkTail = 0;
String   logBuffer;               /* 이번 틱 조립용 임시 버퍼 */
uint32_t lastLogged = 0;
uint32_t lastStatus = 0;

/* --- 세션 통계 --- */
char     logFileName[24];
uint32_t rowCount = 0;
uint32_t cycleCount = 0;
uint32_t missedCount = 0;
float    lastTemp = 0;
uint32_t sessionStart = 0;
bool     paused = false;
uint32_t resyncUntil = 0;
uint32_t writeErrors = 0;
uint16_t lastFileNum = 0;
uint32_t startCountdownEnd = 0;

/* [8] 실행 중 바뀌는 블록 길이. 0 이면 수동 모드(자동 종료 없음) */
uint32_t autoBlockMs = START_IN_MANUAL ? 0UL : AUTO_BLOCK_MS;

bool     btnPrev[2] = {false, false};
uint32_t btnTime[2] = {0, 0};
uint32_t btnDownTime[2] = {0, 0};   /* [8] 길게 누름 판정용 */

/* [9] 라벨 입력 */
String   sessionLabel = "";       /* 이번 블록의 향 이름. 비어 있으면 번호만 */
String   cmdBuf = "";             /* 시리얼 입력 누적 버퍼 */
uint32_t lastCharMs = 0;          /* 줄바꿈이 없을 때의 자동 처리용 */
#define  CMD_TIMEOUT_MS 150

static SdFat sd;

static void panicLeds(void);
static void appendFile(const String& data);
static bool createNewLogFile(void);
static void handleSerialCommand(void);
static void printStatus(void);
static void startNewSession(void);
static void enqueueChunk(const String& data);
static void flushAgedChunks(void);
static void discardPendingChunks(const char* reason);
static void handleButtons(void);
static void updateLed(void);
static void doPauseToggle(void);
static void toggleBlockMode(void);
static void processCommand(String cmd);
static void setSessionLabel(const String& raw);
static void scanExistingFiles(void);
static void blinkTimes(uint8_t n, uint16_t on_ms, uint16_t off_ms);

void setup(void)
{
  Serial.begin(115200);
  commMuxBegin(Wire, SPI);
  pinMode(PANIC_LED, OUTPUT);
  delay(100);

  Serial.println();
  Serial.println(F("=== BME688 다중 세션 로거 ==="));

  /* --- SD 초기화 --- */
  if (!sd.begin(SD_PIN_CS, SPI_EIGHTH_SPEED)) {
    Serial.println(F("[ERROR] SD 카드를 찾을 수 없음"));
    panicLeds();
  }

  /* [9] 라벨이 붙은 파일(LOG_0031_Woody.csv)이 있어도 번호가 겹치지 않도록
   * SD 안의 가장 큰 번호를 먼저 찾아둔다. */
  scanExistingFiles();

  /* --- [1] 새 로그 파일 생성 (기존 파일 삭제하지 않음) --- */
  if (!createNewLogFile()) {
    Serial.println(F("[ERROR] 로그 파일 생성 실패"));
    panicLeds();
  }

  /* --- 센서 8개 초기화 --- */
  for (uint8_t i = 0; i < N_KIT_SENS; i++) {
    commSetup[i] = commMuxSetConfig(Wire, SPI, i, commSetup[i]);
    bme[i].begin(BME68X_SPI_INTF, commMuxRead, commMuxWrite, commMuxDelay,
                 &commSetup[i]);
    if (bme[i].checkStatus()) {
      Serial.println("[ERROR] 센서 " + String(i) + " 초기화 실패: "
                     + bme[i].statusString());
      panicLeds();
    }
  }

  /* --- 히터 프로파일 (원본과 동일하게 유지! 절대 바꾸지 말 것) ---
   * 이 값을 바꾸면 지금까지 모은 모든 데이터와 호환이 깨진다. */
  for (uint8_t i = 0; i < N_KIT_SENS; i++) {
    bme[i].setTPH();

    uint16_t tempProf[10] = {320, 100, 100, 100, 200, 200, 200, 320, 320, 320};
    uint16_t mulProf[10]  = {5, 2, 10, 30, 5, 5, 5, 5, 5, 5};
    uint16_t sharedHeatrDur =
        MEAS_DUR - (bme[i].getMeasDur(BME68X_PARALLEL_MODE) / INT64_C(1000));

    bme[i].setHeaterProf(tempProf, mulProf, sharedHeatrDur, 10);
    bme[i].setOpMode(BME68X_PARALLEL_MODE);

    if (i == 0) {
      uint32_t cycleMs = 0;
      for (uint8_t s = 0; s < 10; s++) cycleMs += sharedHeatrDur * mulProf[s];
      Serial.println("사이클 길이(계산): 약 " + String(cycleMs) + " ms");
    }
  }

  /* [7] 버튼 입력 준비 */
  if (BTN_START >= 0) pinMode(BTN_START, BTN_ACTIVE_LOW ? INPUT_PULLUP : INPUT);
  if (BTN_NEXT  >= 0) pinMode(BTN_NEXT,  BTN_ACTIVE_LOW ? INPUT_PULLUP : INPUT);

  Serial.println();
  Serial.print(F("블록 모드: "));
  if (autoBlockMs) {
    Serial.print(F("자동 ("));
    Serial.print(autoBlockMs / 1000);
    Serial.println(F("초 뒤 자동 저장)"));
  } else {
    Serial.println(F("수동 (시간 제한 없음 - SW_NEXT 누를 때까지 계속 기록)"));
  }
  Serial.println(F("조작: SW_START=시작/정지  SW_NEXT=종료(짧게)/모드전환(2초)"));
  Serial.println(F("      시리얼: p=시작/정지  n=종료  a=모드전환  s=상태"));
  Serial.println(F("      향 이름 입력(citrus/floral/woody/air) → 파일명에 자동 반영"));
  Serial.println(F("경과 | 사이클 | 놓침 | 보드온도"));
  lastStatus = millis();
  sessionStart = millis();
}

void loop(void)
{
  uint8_t nFieldsLeft = 0;
  int16_t indexDiff;
  bool    newLogdata = false;

  handleSerialCommand();          /* [4] */
  handleButtons();                /* [7] */
  updateLed();                    /* [7] */

  /* [7] 시작 카운트다운이 끝나면 실제 녹화 개시 */
  if (startCountdownEnd && (int32_t)(millis() - startCountdownEnd) >= 0) {
    startCountdownEnd = 0;
    paused = false;
    logBuffer = "";
    discardPendingChunks("카운트다운 종료");
    sessionStart = millis();
    lastLogged = millis();
    lastStatus = millis();
    resyncUntil = millis() + RESYNC_MS;
    Serial.println(F(">>> 녹화 시작"));
  }

  /* [8] 자동 모드일 때만 시간 경과로 블록 종료. autoBlockMs==0 이면 무제한 */
  if (!paused && !startCountdownEnd && autoBlockMs &&
      (millis() - sessionStart) >= autoBlockMs) {
    Serial.print(F(">>> "));
    Serial.print(autoBlockMs / 1000);
    Serial.println(F("초 경과 - 블록 자동 종료"));
    startNewSession();
    paused = true;
    blinkTimes(3, 400, 250);      /* 블록 완료 신호 */
  }

  if (!paused && !startCountdownEnd && (millis() - lastLogged) >= MEAS_DUR) {
    lastLogged = millis();

    for (uint8_t i = 0; i < N_KIT_SENS; i++) {
      if (bme[i].fetchData()) {
        do {
          nFieldsLeft = bme[i].getData(sensorData[i]);

          if (sensorData[i].status & BME68X_NEW_DATA_MSK) {

            /* [6] 전환/재개 직후 RESYNC_MS 동안은 추종만 하고 세지 않는다 */
            if ((int32_t)(millis() - resyncUntil) < 0) {
              /* 유예 구간 */
            } else {
              indexDiff = (int16_t)sensorData[i].meas_index
                        - (int16_t)lastMeasindex[i];
              if (indexDiff > 1) {
                missedCount += (indexDiff - 1);
              }
            }
            lastMeasindex[i] = sensorData[i].meas_index;

            if (i == 0 && sensorData[i].gas_index == 9) cycleCount++;
            if (i == 0) lastTemp = sensorData[i].temperature;

            /* --- CSV 한 줄 조립 (컬럼 순서는 원본과 동일) --- */
            logBuffer += millis();                  logBuffer += ",";
            logBuffer += i;                         logBuffer += ",";
            logBuffer += sensorData[i].temperature; logBuffer += ",";
            logBuffer += sensorData[i].pressure;    logBuffer += ",";
            logBuffer += sensorData[i].humidity;    logBuffer += ",";
            logBuffer += sensorData[i].gas_resistance; logBuffer += ",";
            logBuffer += sensorData[i].gas_index;   logBuffer += ",";
            logBuffer += sensorData[i].meas_index;  logBuffer += ",";
            logBuffer += sensorData[i].idac;        logBuffer += ",";
            logBuffer += String(sensorData[i].status, HEX); logBuffer += ",";
            logBuffer += (sensorData[i].status & BME68X_GASM_VALID_MSK); logBuffer += ",";
            logBuffer += (sensorData[i].status & BME68X_HEAT_STAB_MSK);
            logBuffer += "\r\n";

            rowCount++;
            newLogdata = true;
          }
        } while (nFieldsLeft);
      }
    }
  }

  if (newLogdata) {
    enqueueChunk(logBuffer);          /* [5] 바로 쓰지 않고 보류 버퍼에 */
    logBuffer = "";
  }
  if (!paused) flushAgedChunks();     /* [5] 10초 지난 것만 SD로 */

  /* --- [3] 10초마다 진행 요약 한 줄 --- */
  if ((millis() - lastStatus) >= STATUS_INTERVAL) {
    lastStatus = millis();
    printStatus();
  }
}

/*!
 * @brief [4] 시리얼 명령 처리
 */
static void handleSerialCommand(void)
{
  /* [9] 한 글자 명령과 여러 글자 라벨을 모두 받기 위해 토큰 단위로 모은다.
   * 줄바꿈이 오면 즉시 처리하고, 줄바꿈 설정이 꺼져 있으면
   * 입력이 멈춘 뒤 CMD_TIMEOUT_MS 에 자동 처리한다. */
  while (Serial.available()) {
    char ch = Serial.read();
    lastCharMs = millis();

    if (ch == '\n' || ch == '\r') {
      if (cmdBuf.length()) { processCommand(cmdBuf); cmdBuf = ""; }
    } else if (ch != ' ' && ch != '\t') {
      if (cmdBuf.length() < 24) cmdBuf += ch;
    }
  }

  if (cmdBuf.length() && (millis() - lastCharMs) >= CMD_TIMEOUT_MS) {
    processCommand(cmdBuf);
    cmdBuf = "";
  }
}

/*!
 * @brief [9] 모인 토큰을 명령 또는 라벨로 해석한다.
 *        한 글자면 명령, 그보다 길면 향 이름으로 본다.
 *        (그래서 'a'=모드전환 과 'air'=라벨 이 충돌하지 않는다)
 */
static void processCommand(String cmd)
{
  cmd.trim();
  if (!cmd.length()) return;

  if (cmd.length() == 1) {
    char c = tolower(cmd[0]);
    if (c == 'n') { startNewSession(); return; }
    if (c == 'p') { doPauseToggle();   return; }
    if (c == 'a') { toggleBlockMode(); return; }
    if (c == 's') { printStatus();     return; }
    Serial.println(F("명령: p=시작/정지  n=종료  a=모드전환  s=상태"));
    Serial.println(F("향 이름을 치면 이번 블록의 라벨이 됩니다 (예: citrus)"));
    return;
  }
  setSessionLabel(cmd);
}

/*!
 * @brief [9] 이번 블록의 라벨을 정한다. 파일명에 쓸 수 있게 다듬는다.
 */
static void setSessionLabel(const String& raw)
{
  String v;
  for (uint8_t i = 0; i < raw.length(); i++) {
    char c = raw[i];
    /* ASCII 영문/숫자만 남긴다. 한글 등은 파일명에서 깨질 수 있어 제외. */
    if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
        (c >= '0' && c <= '9')) {
      v += c;
    }
  }
  if (!v.length()) {
    Serial.println(F("!! 라벨은 영문/숫자로 입력하세요 (예: citrus)"));
    return;
  }
  if (v.length() > 12) v = v.substring(0, 12);
  v.setCharAt(0, toupper(v[0]));

  sessionLabel = v;
  Serial.print(F(">>> 라벨: "));
  Serial.print(sessionLabel);
  Serial.print(F("   → 저장 시 LOG_xxxx_"));
  Serial.print(sessionLabel);
  Serial.println(F(".csv"));
}

/*!
 * @brief [9] SD 안의 LOG_ 파일 중 가장 큰 번호를 찾아 lastFileNum 에 넣는다.
 *        라벨이 붙어 이름이 바뀐 파일 때문에 번호가 재사용되는 것을 막는다.
 */
static void scanExistingFiles(void)
{
  File dir, f;
  if (!dir.open("/")) return;

  char name[64];
  while (f.openNext(&dir, O_RDONLY)) {
    if (f.getName(name, sizeof(name))) {
      unsigned int n = 0;
      if (sscanf(name, "LOG_%u", &n) == 1 && n > lastFileNum) {
        lastFileNum = (uint16_t)n;
      }
    }
    f.close();
  }
  dir.close();

  if (lastFileNum) {
    Serial.print(F("기존 파일 최대 번호: "));
    Serial.println(lastFileNum);
  }
}

/*!
 * @brief [8] 수동 ↔ 자동 블록 모드 전환.
 *        수동: 시간 제한 없이 SW_NEXT 를 누를 때까지 계속 기록
 *        자동: AUTO_BLOCK_MS 경과 시 저장 후 정지
 *        녹화 중에 바꿔도 되며, 현재 블록에 즉시 적용된다.
 */
static void toggleBlockMode(void)
{
  autoBlockMs = autoBlockMs ? 0UL : AUTO_BLOCK_MS;

  Serial.println();
  if (autoBlockMs) {
    Serial.print(F(">>> 자동 모드: "));
    Serial.print(autoBlockMs / 1000);
    Serial.println(F("초마다 자동 저장 후 정지"));
    blinkTimes(4, 120, 120);
  } else {
    Serial.println(F(">>> 수동 모드: 시간 제한 없음 (SW_NEXT 로 직접 종료)"));
    blinkTimes(2, 120, 200);
  }
}

/*!
 * @brief [4] 현재 파일을 안전하게 닫고 다음 번호 파일로 전환.
 *        전원을 끄지 않으므로 보드의 열적 상태가 유지된다.
 */
static void startNewSession(void)
{
  /* [5] 명령 직전 10초는 오염 가능성이 있으므로 기록하지 않고 폐기 */
  logBuffer = "";
  discardPendingChunks("세션 전환");

  uint32_t sec = (millis() - sessionStart) / 1000;

  Serial.println();
  Serial.println(F("--- 세션 종료 ---"));
  Serial.print(F("  파일   : ")); Serial.println(logFileName);
  Serial.print(F("  기록시간: "));
  Serial.print(sec); Serial.print(F(" s ("));
  Serial.print(sec / 60); Serial.print(F("분 "));
  Serial.print(sec % 60); Serial.println(F("초)"));
  Serial.print(F("  사이클 : ")); Serial.println(cycleCount);
  Serial.print(F("  놓침   : ")); Serial.println(missedCount);
  if (writeErrors) {
    Serial.print(F("  쓰기실패: ")); Serial.println(writeErrors);
  }

  /* [9] 라벨이 있으면 파일명에 붙인다. 모든 쓰기가 끝난 지금이 안전한 시점. */
  if (sessionLabel.length()) {
    char newName[48];
    snprintf(newName, sizeof(newName), "/LOG_%04u_%s.csv",
             lastFileNum, sessionLabel.c_str());
    if (sd.exists(newName)) {
      Serial.print(F("  !! 같은 이름이 이미 있어 그대로 둠: "));
      Serial.println(newName);
    } else if (sd.rename(logFileName, newName)) {
      Serial.print(F("  저장   : ")); Serial.println(newName);
    } else {
      Serial.println(F("  !! 파일명 변경 실패 (번호 이름 그대로 저장됨)"));
    }
    sessionLabel = "";            /* 다음 블록은 다시 입력받는다 */
  } else {
    Serial.println(F("  (라벨 없음 - 향 이름을 치면 파일명에 붙습니다)"));
  }

  /* 세션 통계 초기화 */
  cycleCount = 0;
  missedCount = 0;
  writeErrors = 0;
  rowCount = 0;
  for (uint8_t i = 0; i < N_KIT_SENS; i++) lastMeasindex[i] = 0;

  if (!createNewLogFile()) {
    Serial.println(F("[ERROR] 새 파일 생성 실패 — SD 확인 필요. 기록 중단"));
    paused = true;
    return;
  }

  sessionStart = millis();
  lastStatus = millis();
  lastLogged = millis();
  paused = true;                        /* 저장 후 자동 정지 */
  startCountdownEnd = 0;
  resyncUntil = millis() + RESYNC_MS;
  Serial.println(F("--- 정지 상태. 향 세팅 후 SW_START (또는 p) ---"));
}

/*!
 * @brief [5] 이번 틱 데이터를 보류 링버퍼에 넣는다.
 *        버퍼가 가득 차면 가장 오래된 것을 먼저 SD로 밀어낸다.
 */
static void enqueueChunk(const String& data)
{
  uint8_t next = (chunkHead + 1) % N_CHUNKS;
  if (next == chunkTail) {
    appendFile(chunkData[chunkTail]);
    chunkData[chunkTail] = "";
    chunkTail = (chunkTail + 1) % N_CHUNKS;
  }
  chunkData[chunkHead] = data;
  chunkTime[chunkHead] = millis();
  chunkHead = next;
}

/*!
 * @brief [5] 보류된 지 DISCARD_WINDOW_MS 가 지난 청크만 SD에 기록.
 */
static void flushAgedChunks(void)
{
  bool wrote = false;
  while (chunkTail != chunkHead &&
         (millis() - chunkTime[chunkTail]) >= DISCARD_WINDOW_MS) {
    if (!wrote) { digitalWrite(PANIC_LED, HIGH); wrote = true; }
    appendFile(chunkData[chunkTail]);
    chunkData[chunkTail] = "";
    chunkTail = (chunkTail + 1) % N_CHUNKS;
  }
  if (wrote) digitalWrite(PANIC_LED, LOW);
}

/*!
 * @brief [5] 보류 중인 청크 전부 폐기 (= 최근 10초 삭제).
 */
static void discardPendingChunks(const char* reason)
{
  uint16_t n = 0;
  while (chunkTail != chunkHead) {
    chunkData[chunkTail] = "";
    chunkTail = (chunkTail + 1) % N_CHUNKS;
    n++;
  }
  Serial.print(F(">>> 최근 10초 데이터 "));
  Serial.print(n);
  Serial.print(F("틱 폐기 ("));
  Serial.print(reason);
  Serial.println(F(")"));
}

/*!
 * @brief [7] 정지/재개 토글. 재개는 곧바로가 아니라 카운트다운 후 시작한다
 *        (손을 떼고 물러날 시간을 줘서 접근 오염을 기록하지 않기 위함).
 */
static void doPauseToggle(void)
{
  if (startCountdownEnd) {              /* 카운트다운 중이면 취소 */
    startCountdownEnd = 0;
    paused = true;
    Serial.println(F(">>> 시작 취소 (정지 유지)"));
    return;
  }

  if (!paused) {
    paused = true;
    logBuffer = "";
    discardPendingChunks("일시정지");
    Serial.println(F(">>> 일시정지"));
  } else {
    startCountdownEnd = millis() + START_DELAY_MS;
    Serial.print(F(">>> "));
    Serial.print(START_DELAY_MS / 1000);
    Serial.println(F("초 후 녹화 시작 - 손을 떼고 물러나세요"));
  }
}

/*!
 * @brief [7][8] 버튼 처리.
 *        SW_START : 누르는 순간 동작 (시작/정지)
 *        SW_NEXT  : 떼는 순간 동작. 누른 시간이 LONGPRESS_MS 이상이면 모드 전환,
 *                   그보다 짧으면 블록 종료. (길게/짧게를 구분하려면 뗄 때 판정)
 */
static void handleButtons(void)
{
  const int pins[2] = {BTN_START, BTN_NEXT};

  for (uint8_t i = 0; i < 2; i++) {
    if (pins[i] < 0) continue;

    bool pressed = (digitalRead(pins[i]) == (BTN_ACTIVE_LOW ? LOW : HIGH));
    if (pressed == btnPrev[i]) continue;
    if ((millis() - btnTime[i]) < DEBOUNCE_MS) continue;

    btnTime[i] = millis();
    btnPrev[i] = pressed;

    if (i == 0) {                       /* --- SW_START : 누를 때 --- */
      if (pressed) doPauseToggle();

    } else {                            /* --- SW_NEXT : 뗄 때 판정 --- */
      if (pressed) {
        btnDownTime[i] = millis();      /* 누른 시각 기록 */
        continue;
      }

      uint32_t held = millis() - btnDownTime[i];

      if (held >= LONGPRESS_MS) {       /* [8] 길게 = 모드 전환 */
        toggleBlockMode();
        continue;
      }

      /* 짧게 = 블록 종료. 실수로 두 번 눌러 빈 파일이 생기는 것을 막는다 */
      if (!paused && (millis() - sessionStart) < 10000UL) {
        Serial.println(F("!! 블록이 너무 짧음 - 무시"));
        continue;
      }
      startNewSession();
      paused = true;
      blinkTimes(3, 400, 250);
    }
  }
}

/*!
 * @brief [7] LED 로 상태 표시. 시리얼 없이도 지금 뭘 하는지 알 수 있게.
 */
static void updateLed(void)
{
  if (startCountdownEnd) {
    digitalWrite(PANIC_LED, ((millis() / 100) % 2) ? HIGH : LOW);
  } else if (paused) {
    digitalWrite(PANIC_LED, ((millis() / 1000) % 2) ? HIGH : LOW);
  }
  /* 녹화 중에는 flushAgedChunks 가 기록 시점에 반짝인다 */
}

/*!
 * @brief 확인용 점멸 (블로킹). 조작 직후에만 쓰므로 데이터 영향 없음.
 */
static void blinkTimes(uint8_t n, uint16_t on_ms, uint16_t off_ms)
{
  for (uint8_t i = 0; i < n; i++) {
    digitalWrite(PANIC_LED, HIGH); delay(on_ms);
    digitalWrite(PANIC_LED, LOW);  delay(off_ms);
  }
}

/*!
 * @brief 진행 상황 한 줄 요약. 긴 녹화를 위해 분 단위도 함께 표시한다.
 */
static void printStatus(void)
{
  uint32_t sec = (millis() - sessionStart) / 1000;
  String t = String(sec) + "s";
  if (sec >= 60) t += "(" + String(sec / 60) + "m" + String(sec % 60) + "s)";

  Serial.println(t + " | "
               + String(cycleCount) + " | "
               + String(missedCount) + " | "
               + String(lastTemp, 1) + "C"
               + (sessionLabel.length() ? "  <" + sessionLabel + ">" : "")
               + (autoBlockMs ? "" : " [수동]")
               + (paused ? "  [정지중]" : "")
               + (missedCount > 0 ? "  <-- 놓침 발생" : "")
               + (writeErrors > 0 ? "  <-- SD 쓰기실패" : ""));
}

/*!
 * @brief [1] 비어 있는 번호를 찾아 새 로그 파일을 만들고 헤더를 쓴다.
 *        기존 파일은 건드리지 않는다.
 */
static bool createNewLogFile(void)
{
  /* [6] 1번부터 매번 훑으면 세션이 늘수록 느려진다. 직전 번호 다음부터 탐색 */
  for (uint16_t n = lastFileNum + 1; n <= MAX_LOG_FILES; n++) {
    snprintf(logFileName, sizeof(logFileName), "/LOG_%04u.csv", n);

    if (!sd.exists(logFileName)) {
      File file;
      if (!file.open(logFileName, (O_RDWR | O_CREAT))) {
        return false;
      }
      bool ok = file.println(
        "TimeStamp(ms),Sensor Index,Temperature(deg C),Pressure(Pa),"
        "Humidity(%),Gas Resistance(ohm),Gas Index,Meas Index,idac,"
        "Status,Gas Valid,Heater Stable");
      file.close();

      if (ok) {
        lastFileNum = n;
        Serial.print(F("새 세션 파일: "));
        Serial.println(logFileName);
        return true;
      }
      return false;
    }
  }
  Serial.println(F("[ERROR] 로그 파일 번호가 꽉 찼음 (SD 정리 필요)"));
  return false;
}

/*!
 * @brief 로그 파일 끝에 데이터를 덧붙인다.
 *        쓰기 실패는 정지시키지 않고 카운트만 한다 [2].
 */
static void appendFile(const String& data)
{
  File file;
  if (!file.open(logFileName, (O_RDWR | O_AT_END))) {
    writeErrors++;              /* [6] 놓침이 아니라 SD 쓰기 문제 */
    return;
  }
  if (!file.print(data)) {
    writeErrors++;
  }
  file.close();
}

/*!
 * @brief 치명적 초기화 실패 시에만 사용. LED 1초 점멸 후 정지.
 */
static void panicLeds(void)
{
  while (1) {
    digitalWrite(PANIC_LED, HIGH);
    delay(PANIC_DUR);
    digitalWrite(PANIC_LED, LOW);
    delay(PANIC_DUR);
  }
}
