// air_control — 공기청정기(FAN) 버튼 릴레이 + 초음파 분무기 3채널 (Arduino Uno)
//
// 명령 (한 줄, 9600 baud, 대소문자 무관)
//   MIST OFF            분무 전부 끔
//   MIST MNT            페퍼민트만        (MNT=페퍼민트 D3, LVD=라벤더 D4, ORG=오렌지 D5)
//   MIST MNT+ORG        이름을 + 로 이으면 함께 켬 (순서 무관)
//   MIST ALL            셋 다
//   FAN ON / FAN OFF    청정기 켜기 / 끄기
//   FAN MODE            청정기 모드 1→2→3→1
//   SYNC0-3             청정기 상태만 맞춤 (버튼은 안 누름)  0=꺼짐, 1-3=켜짐·모드
//   POL0 / POL1         분무 릴레이 극성 (POL0 = LOW 가 켜짐, 기본)
//   STAT                현재 상태 한 줄
//
// 켜진 채널은 끊김 없이 계속 분무한다 (세기는 항상 최대. 세기 조절은 아직 없음).
//
// 옛 숫자 명령도 받는다: ONxyz / OFFxyz / Mxyz / MODE  (자리 숫자가 0 이 아니면 그 채널 켬)
//
// loop 는 멈추지 않는다. 청정기 버튼 누르기(0.2초·2.5초)도 millis 로 처리해
// 그동안에도 분무와 명령 수신이 계속 돈다.

#define FW_VERSION "air_control 2.1 named"

const uint8_t FAN_PIN = 2;
const uint8_t MIST_PINS[3] = {3, 4, 5};
const char *const MIST_NAMES[3] = {"MNT", "LVD", "ORG"};

// 세 분무기를 같은 순간에 켜면 돌입 전류로 하나도 안 켜진다 (2026-11-10 실측).
// 채널을 켤 때마다 직전에 켠 채널과 이만큼 간격을 둔다.
const unsigned long MIST_STAGGER_MS = 300;

// 청정기 버튼: 짧게 = 켜기·모드 바꾸기, 길게 = 끄기. 누름 사이에는 간격을 둔다.
const unsigned long FAN_PRESS_SHORT_MS = 200;
const unsigned long FAN_PRESS_LONG_MS = 2500;
const unsigned long FAN_PRESS_GAP_MS = 300;
const uint8_t FAN_QUEUE_SIZE = 4;

// ---------------------------------------------------------------- 상태
uint8_t relayOn = LOW;
uint8_t relayOff = HIGH;

bool mistWant[3] = {false, false, false};  // 명령으로 정한 목표
bool mistOut[3] = {false, false, false};   // 지금 핀에 나가 있는 값
unsigned long mistLastTurnOn = 0;
bool mistStaggerArmed = false;

bool fanIsOn = false;
uint8_t fanMode = 1;

const uint8_t FAN_IDLE = 0, FAN_PRESSING = 1, FAN_GAP = 2;
uint8_t fanPhase = FAN_IDLE;
unsigned long fanPhaseStart = 0;
unsigned long fanPressMs = 0;
unsigned long fanQueue[FAN_QUEUE_SIZE];
uint8_t fanQueueHead = 0;
uint8_t fanQueueCount = 0;

char lineBuf[24];
uint8_t lineLen = 0;
bool lineOverflow = false;

// ---------------------------------------------------------------- 분무
void writeMist(uint8_t i, bool on) {
  digitalWrite(MIST_PINS[i], on ? relayOn : relayOff);
  mistOut[i] = on;
}

void printMistState() {
  uint8_t count = mistWant[0] + mistWant[1] + mistWant[2];
  Serial.print(F("MIST "));
  if (count == 0) {
    Serial.println(F("OFF"));
  } else if (count == 3) {
    Serial.println(F("ALL"));
  } else {
    bool first = true;
    for (uint8_t i = 0; i < 3; i++) {
      if (!mistWant[i]) continue;
      if (!first) Serial.print('+');
      Serial.print(MIST_NAMES[i]);
      first = false;
    }
    Serial.println();
  }
}

void setMist(bool a, bool b, bool c) {
  mistWant[0] = a;
  mistWant[1] = b;
  mistWant[2] = c;
  // 끄는 것은 바로. 켜는 것은 updateMist 가 간격을 두고 켠다. 이미 켜진 채널은 그대로 이어 간다.
  for (uint8_t i = 0; i < 3; i++) {
    if (!mistWant[i] && mistOut[i]) writeMist(i, false);
  }
}

void updateMist() {
  unsigned long now = millis();
  for (uint8_t i = 0; i < 3; i++) {
    if (mistWant[i] && !mistOut[i]) {
      if (mistStaggerArmed && now - mistLastTurnOn < MIST_STAGGER_MS) return;
      writeMist(i, true);
      mistLastTurnOn = now;
      mistStaggerArmed = true;
    } else if (!mistWant[i] && mistOut[i]) {
      writeMist(i, false);
    }
  }
}

// "MNT+ORG" / "ALL" / "OFF" → 채널 3개. 모르는 이름이면 false (상태는 안 바꾼다).
bool parseMistNames(const char *args, bool out[3]) {
  out[0] = out[1] = out[2] = false;
  bool sawOff = false;
  uint8_t tokens = 0;
  const char *p = args;

  while (true) {
    while (*p == ' ' || *p == '+') p++;
    if (*p == '\0') break;
    const char *start = p;
    while (*p != '\0' && *p != '+' && *p != ' ') p++;
    uint8_t len = p - start;
    tokens++;

    if (len != 3) return false;
    if (strncmp(start, "ALL", 3) == 0) {
      out[0] = out[1] = out[2] = true;
    } else if (strncmp(start, "OFF", 3) == 0) {
      sawOff = true;
    } else {
      bool known = false;
      for (uint8_t i = 0; i < 3; i++) {
        if (strncmp(start, MIST_NAMES[i], 3) == 0) {
          out[i] = true;
          known = true;
        }
      }
      if (!known) return false;
    }
  }

  if (tokens == 0) return false;
  if (sawOff) return tokens == 1;  // OFF 는 혼자만
  return true;
}

// ---------------------------------------------------------------- 청정기
bool queueFanPress(unsigned long ms) {
  if (fanQueueCount >= FAN_QUEUE_SIZE) return false;
  fanQueue[(fanQueueHead + fanQueueCount) % FAN_QUEUE_SIZE] = ms;
  fanQueueCount++;
  return true;
}

void updateFan() {
  unsigned long now = millis();
  if (fanPhase == FAN_IDLE) {
    if (fanQueueCount == 0) return;
    fanPressMs = fanQueue[fanQueueHead];
    fanQueueHead = (fanQueueHead + 1) % FAN_QUEUE_SIZE;
    fanQueueCount--;
    digitalWrite(FAN_PIN, LOW);
    fanPhase = FAN_PRESSING;
    fanPhaseStart = now;
  } else if (fanPhase == FAN_PRESSING) {
    if (now - fanPhaseStart >= fanPressMs) {
      digitalWrite(FAN_PIN, HIGH);
      fanPhase = FAN_GAP;
      fanPhaseStart = now;
    }
  } else if (now - fanPhaseStart >= FAN_PRESS_GAP_MS) {
    fanPhase = FAN_IDLE;
  }
}

void printFanOnMode() {
  Serial.print(F("FAN ON / MODE "));
  Serial.println(fanMode);
}

void fanOn() {
  if (fanIsOn) {
    Serial.println(F("FAN 이미 ON 상태"));
  } else if (queueFanPress(FAN_PRESS_SHORT_MS)) {
    fanIsOn = true;
    printFanOnMode();
  } else {
    Serial.println(F("FAN 바쁨"));
  }
}

void fanOff() {
  if (!fanIsOn) {
    Serial.println(F("FAN 이미 OFF 상태"));
  } else if (queueFanPress(FAN_PRESS_LONG_MS)) {
    fanIsOn = false;
    Serial.println(F("FAN OFF"));
  } else {
    Serial.println(F("FAN 바쁨"));
  }
}

void fanModeChange() {
  if (!fanIsOn) {
    Serial.println(F("FAN OFF 상태라 MODE 불가"));
  } else if (queueFanPress(FAN_PRESS_SHORT_MS)) {
    fanMode = fanMode >= 3 ? 1 : fanMode + 1;
    Serial.print(F("FAN MODE "));
    Serial.println(fanMode);
  } else {
    Serial.println(F("FAN 바쁨"));
  }
}

// ---------------------------------------------------------------- 명령
bool isMistDigits(const char *s) {
  for (uint8_t i = 0; i < 3; i++) {
    if (s[i] < '0' || s[i] > '9') return false;
  }
  return true;
}

// 옛 숫자 3자리: 0 이 아니면 켬
void setMistFromDigits(const char *d) {
  setMist(d[0] != '0', d[1] != '0', d[2] != '0');
  printMistState();
}

void printStat() {
  Serial.print(F("STAT FAN="));
  Serial.print(fanIsOn ? F("ON") : F("OFF"));
  Serial.print(F(" MODE="));
  Serial.print(fanMode);
  Serial.print(F(" POL="));
  Serial.print(relayOn == LOW ? 0 : 1);
  // 분무 핀을 실제로 되읽은 값 (L=0V 쪽, H=5V 쪽). 켠 채널은 L 이어야 한다.
  // 켰는데 H 로 읽히면 핀이 전류를 못 이겨 전압이 올라간 것 (핀 과부하).
  Serial.print(F(" PINS="));
  for (uint8_t i = 0; i < 3; i++) Serial.print(digitalRead(MIST_PINS[i]) ? 'H' : 'L');
  Serial.print(F(" FW="));
  Serial.print(F(FW_VERSION));
  Serial.print(F(" / "));
  printMistState();
}

void handleCommand(const char *cmd) {
  uint8_t len = strlen(cmd);

  Serial.print(F("입력값: ["));
  Serial.print(cmd);
  Serial.println(F("]"));

  if (strncmp(cmd, "MIST ", 5) == 0) {
    bool next[3];
    if (parseMistNames(cmd + 5, next)) {
      setMist(next[0], next[1], next[2]);
      printMistState();
    } else {
      Serial.println(F("명령어 오류"));
    }
  } else if (strcmp(cmd, "FAN ON") == 0) {
    fanOn();
  } else if (strcmp(cmd, "FAN OFF") == 0) {
    fanOff();
  } else if (strcmp(cmd, "FAN MODE") == 0 || strcmp(cmd, "MODE") == 0) {
    fanModeChange();
  } else if (len == 5 && strncmp(cmd, "SYNC", 4) == 0 && cmd[4] >= '0' && cmd[4] <= '3') {
    uint8_t n = cmd[4] - '0';
    if (n == 0) {
      fanIsOn = false;
      Serial.println(F("FAN OFF"));
    } else {
      fanIsOn = true;
      fanMode = n;
      printFanOnMode();
    }
  } else if (strcmp(cmd, "POL0") == 0 || strcmp(cmd, "POL1") == 0) {
    bool lowOn = cmd[3] == '0';
    bool was[3] = {mistOut[0], mistOut[1], mistOut[2]};
    relayOn = lowOn ? LOW : HIGH;
    relayOff = lowOn ? HIGH : LOW;
    for (uint8_t i = 0; i < 3; i++) writeMist(i, was[i]);  // 새 극성으로 다시 쓴다
    Serial.println(lowOn ? F("MIST 극성 LOW=ON") : F("MIST 극성 HIGH=ON"));
  } else if (strcmp(cmd, "STAT") == 0) {
    printStat();
  }
  // ---- 옛 숫자 명령
  else if (len == 5 && strncmp(cmd, "ON", 2) == 0 && isMistDigits(cmd + 2)) {
    fanOn();
    setMistFromDigits(cmd + 2);
  } else if (len == 6 && strncmp(cmd, "OFF", 3) == 0 && isMistDigits(cmd + 3)) {
    fanOff();
    setMistFromDigits(cmd + 3);
  } else if (len == 4 && cmd[0] == 'M' && isMistDigits(cmd + 1)) {
    setMistFromDigits(cmd + 1);
  } else {
    Serial.println(F("명령어 오류"));
  }
}

void readSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      if (lineOverflow) {
        Serial.println(F("명령어 오류"));
      } else {
        while (lineLen > 0 && lineBuf[lineLen - 1] == ' ') lineLen--;
        lineBuf[lineLen] = '\0';
        if (lineLen > 0) handleCommand(lineBuf);
      }
      lineLen = 0;
      lineOverflow = false;
      continue;
    }
    if (lineLen == 0 && c == ' ') continue;
    if (lineLen < sizeof(lineBuf) - 1) {
      lineBuf[lineLen++] = toupper(c);
    } else {
      lineOverflow = true;
    }
  }
}

// ---------------------------------------------------------------- 시작
void setup() {
  // 출력으로 바꾸기 전에 '꺼짐' 값을 먼저 써서, 부팅 때 릴레이가 순간 켜지지 않게 한다
  digitalWrite(FAN_PIN, HIGH);
  pinMode(FAN_PIN, OUTPUT);
  for (uint8_t i = 0; i < 3; i++) {
    digitalWrite(MIST_PINS[i], relayOff);
    pinMode(MIST_PINS[i], OUTPUT);
  }

  Serial.begin(9600);
  Serial.println(F("READY"));
  // 'M133 /' 는 브리지가 재부팅을 알아채는 문구라 남겨 둔다
  Serial.println(F("예시: MIST MNT+ORG / MIST ALL / MIST OFF / FAN ON / FAN OFF / FAN MODE / STAT (옛: M133 / ON000)"));
  Serial.println(F("FW " FW_VERSION));
}

void loop() {
  readSerial();
  updateFan();
  updateMist();
}
