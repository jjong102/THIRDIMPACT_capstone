#define FAN_PIN 2

#define MIST1 3
#define MIST2 4
#define MIST3 5

const bool RELAY_ON = LOW;
const bool RELAY_OFF = HIGH;

int mistPins[3] = {MIST1, MIST2, MIST3};

unsigned long runTime[3] = {0, 0, 0};
unsigned long maxTime = 0;

bool fanIsOn = false;
int fanMode = 1;

unsigned long cycleStart = 0;
unsigned long pauseStart = 0;
bool inPause = false;

void setup() {
  Serial.begin(9600);

  pinMode(FAN_PIN, OUTPUT);
  digitalWrite(FAN_PIN, HIGH);

  for (int i = 0; i < 3; i++) {
    pinMode(mistPins[i], OUTPUT);
    digitalWrite(mistPins[i], RELAY_OFF);
  }

  allMistOff();

  Serial.println("READY");
  Serial.println("예시: ON133 / OFF015 / M133 / M000 / MODE / SYNC0-3");
}

void loop() {
  readCommand();
  updateMist();
}

void readCommand() {
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    cmd.toUpperCase();

    Serial.print("입력값: [");
    Serial.print(cmd);
    Serial.println("]");

    if (cmd.startsWith("ON") && cmd.length() == 5) {
      if (!fanIsOn) {
        fanOn();
        fanIsOn = true;
      } else {
        Serial.println("FAN 이미 ON 상태");
      }

      setMist(cmd.substring(2));
    }

    else if (cmd.startsWith("OFF") && cmd.length() == 6) {
      if (fanIsOn) {
        fanOff();
        fanIsOn = false;
      } else {
        Serial.println("FAN 이미 OFF 상태");
      }

      setMist(cmd.substring(3));
    }

    else if (cmd == "MODE") {
      if (fanIsOn) {
        fanModeChange();
      } else {
        Serial.println("FAN OFF 상태라 MODE 불가");
      }
    }

    // Mxyz: 팬은 그대로 두고 미스트만 바꾼다. (MODE 와 겹치지 않게 숫자 3자리만 허용)
    else if (cmd.startsWith("M") && cmd.length() == 4 && isMistDigits(cmd.substring(1))) {
      setMist(cmd.substring(1));
    }

    else if (cmd.startsWith("SYNC") && cmd.length() == 5) {
      int n = cmd.charAt(4) - '0';

      if (n == 0) {
        fanIsOn = false;
        Serial.println("FAN OFF");
      } else if (n >= 1 && n <= 3) {
        fanIsOn = true;
        fanMode = n;
        Serial.print("FAN ON / MODE ");
        Serial.println(fanMode);
      } else {
        Serial.println("명령어 오류");
      }
    }

    else {
      Serial.println("명령어 오류");
    }
  }
}

void fanOn() {
  digitalWrite(FAN_PIN, LOW);
  delay(200);
  digitalWrite(FAN_PIN, HIGH);

  Serial.print("FAN ON / MODE ");
  Serial.println(fanMode);
}

void fanOff() {
  digitalWrite(FAN_PIN, LOW);
  delay(2500);
  digitalWrite(FAN_PIN, HIGH);

  Serial.println("FAN OFF");
}

void fanModeChange() {
  digitalWrite(FAN_PIN, LOW);
  delay(200);
  digitalWrite(FAN_PIN, HIGH);

  fanMode++;
  if (fanMode > 3) fanMode = 1;

  Serial.print("FAN MODE ");
  Serial.println(fanMode);
}

bool isMistDigits(String digits) {
  for (int i = 0; i < 3; i++) {
    if (!isDigit(digits.charAt(i))) return false;
  }
  return true;
}

void setMist(String mistCmd) {
  maxTime = 0;

  for (int i = 0; i < 3; i++) {
    runTime[i] = (mistCmd.charAt(i) - '0') * 1000UL;

    if (runTime[i] > maxTime) {
      maxTime = runTime[i];
    }
  }

  cycleStart = millis();
  inPause = false;

  if (maxTime == 0) {
    allMistOff();
    Serial.println("MIST OFF");
  } else {
    Serial.print("MIST 반복 설정: ");
    Serial.println(mistCmd);
  }
}

void updateMist() {
  if (maxTime == 0) {
    allMistOff();
    return;
  }

  unsigned long now = millis();

  if (inPause) {
    allMistOff();

    if (now - pauseStart >= 1000) {
      inPause = false;
      cycleStart = now;
    }

    return;
  }

  unsigned long elapsed = now - cycleStart;

  if (elapsed >= maxTime) {
    allMistOff();
    inPause = true;
    pauseStart = now;
    return;
  }

  for (int i = 0; i < 3; i++) {
    if (elapsed < runTime[i]) {
      digitalWrite(mistPins[i], RELAY_ON);
    } else {
      digitalWrite(mistPins[i], RELAY_OFF);
    }
  }
}

void allMistOff() {
  for (int i = 0; i < 3; i++) {
    digitalWrite(mistPins[i], RELAY_OFF);
  }
}
