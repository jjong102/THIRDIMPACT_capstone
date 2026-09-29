# HANDOFF — 부스 시연 작업 인수인계

> 새 Claude 세션(젯슨/다른 창)이 이 파일 + [CLAUDE.md](CLAUDE.md)를 읽으면 맥락을 이어갈 수 있도록 정리한 메모.
> 대화 기록 자체는 기기별 로컬 저장이라 이동 불가 → 이 문서가 그 대체.

## 지금 하는 것: 부스 시연 데모 (다음 주)
향 종류 분류 정확도가 낮아, 시연은 **"공기 vs 향 감지"** 이진 판정으로 단순화.
감지 결과에 따라 **공기청정기 작동 → 향기 분사**까지 시연하는 게 목표.

## 전체 데이터 흐름
```
WARD ESP32(감지) → HiveMQ MQTT → 젯슨(파이썬: 구독+시퀀스 판단) → USB시리얼 → 아두이노 우노 → [릴레이=공기청정기] + [초음파가습기=발향]
```

## 이미 완성된 것
- **감지 펌웨어**: `firmware/bme688_demo_air_vs_scent/` (ESP32, 8센서 DevKit)
  - 공기 기준선 대비 가스저항 하락(`resp`, log10)으로 air/scent 판정. 히스테리시스+드리프트 보정.
  - LED: 빠른점멸=보정중 / 느린점멸=공기 / 계속켜짐=향감지. 시리얼 `r`=재보정.
  - 튜닝값: `THRESH_ON=0.08`, `THRESH_OFF=0.04`, `BOOTSTRAP_MS=25000`. 실측 노이즈 ±0.04, 향 대면 resp 0.3+까지 → 잘 분리됨.
  - **MQTT 발행**: 토픽 `sensor/air_quality/ward_demo`, 페이로드 `"air (7%)" / "scent (47%)"` (팀 대시보드 정규식·mqtt_receiver.py 호환). 네트워킹은 core0 전용 태스크라 WiFi 끊겨도 감지 안 멈춤.
  - 자격증명: `firmware/bme688_demo_air_vs_scent/secrets.h` (git-ignore, 실값 채워둠). WiFi는 2.4GHz 핫스팟 — 부스에서 켜야 함(SSID/비번은 secrets.h 참조).

## MQTT 접속 정보
- 브로커: HiveMQ Cloud (Serverless Free 플랜 — 이 규모엔 충분)
- 호스트/포트: `<secrets.h 참조>` : 8883 (TLS)
- 유저/비번: `secrets.h`에 있음 (여기 평문 기재 금지)
- 구독 토픽: `sensor/air_quality/#` (전체) 또는 `sensor/air_quality/ward_demo` (데모만)
- 클라이언트 ID는 기기마다 유일해야 함(중복 시 브로커가 끊음).

## 다음 할 일 (미완성) — 액추에이터 제어
만들 파일 2개:
1. **아두이노 우노 펌웨어** (`firmware/ward_actuator_uno/ward_actuator_uno.ino`)
   - USB 시리얼로 젯슨 명령 수신 → 릴레이(공기청정기) + 초음파가습기(발향) 구동.
2. **젯슨 파이썬 컨트롤러** (`python/ward_controller.py`)
   - MQTT 구독 → air/scent 파싱 → 시퀀스 실행 → 우노에 시리얼 명령.

### 확정 필요한 하드웨어 사항 (사용자 답변 대기 중)
- 초음파 가습기 "3핀" 제어 방식: 신호 1핀인지 3핀인지 / HIGH 유지인지 트리거인지
- 릴레이 Active-LOW 여부
- 데모 시퀀스·타이밍: 예) 향 감지 → 청정기 ON(8초) → 발향(5초) → OFF → 공기 복귀
- 아두이노 핀 번호(기본값 정하고 주석 처리 예정)

## 참고
- 이 프로젝트는 `C:\2026capston`으로 통합됨. Arduino IDE 스케치북 위치를 `C:\2026capston`로 설정해야 라이브러리(`libraries/`)를 찾음.
- 대회 저장소 `jjong102/2026ESWContest_free_THIRDIMPACT`의 `ward_bme688/`에 감지 부분 일부가 올라가 있음(PR #1 머지됨).
