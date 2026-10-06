# air-scent-dashboard

발향부 Jetson에서 도는 터치 대시보드입니다. React + Vite UI와, Vite가 함께 띄우는 Python HTTP 브리지로 구성됩니다.

| 브리지 | 포트 | 역할 |
|---|---|---|
| `server/fragrance_bridge.py` | 5174 | Arduino 발향 컨트롤러 시리얼 제어 |
| `server/mqtt_bridge.py` | 5175 | HiveMQ 공기질(향 분류) 수신 |
| `server/llm_bridge.py` | 5176 | NVIDIA NIM 향 추천 · 발향 명령 MQTT 발행 |
| `server/stt_bridge.py` | 5177 | faster-whisper 음성 인식 |
| `server/gdm_bridge.py` | 5178 | GDM 공기질 맵 |
| `server/ros_nav_bridge.py` | 5179 | Nav2 목표 전송 (`scripts/ros/run_ros_nav_bridge.sh`로 실행) |
| `server/tts_bridge.py` | 5180 | edge-tts 음성 안내 |

## 처음 설정

```bash
cd ~/THIRDIMPACT_capstone/air-scent-dashboard
npm install
cp .env.example .env.local   # 값 채우기
```

`.env.local`은 git에 올라가지 않습니다. 필요한 값:

- `NVIDIA_API_KEY`: NIM API 키 (없으면 LLM은 mock으로 동작)
- `MQTT_BROKER`, `MQTT_USER`, `MQTT_PASS`: HiveMQ Cloud 접속 정보 (없으면 MQTT 브리지가 연결하지 않음)

## 실행

평소에는 systemd 사용자 서비스로 자동 실행됩니다.

```bash
systemctl --user status air-scent-dashboard air-scent-chrome   # 상태
systemctl --user restart air-scent-dashboard                   # 코드 · .env.local 바꾼 뒤
journalctl --user -u air-scent-dashboard -f                    # 로그
```

직접 띄울 때는 서비스를 먼저 멈춘 뒤:

```bash
systemctl --user stop air-scent-dashboard
npm run dev -- --host 0.0.0.0 --port 5173
```

브리지 상태 확인:

```bash
curl -s localhost:5175/api/mqtt/health
curl -s localhost:5176/api/llm/health
```

## GDM ROS 노드

`GDM/`의 `MQTT_sub_node`도 같은 환경변수로 MQTT에 접속합니다. 실행 전에 `.env.local`을 불러오세요.

```bash
set -a; source ~/THIRDIMPACT_capstone/air-scent-dashboard/.env.local; set +a
ros2 run GDM MQTT_sub_node
```

## 발향 펌웨어

Arduino 코드는 저장소 최상위 [`firmware/air_control`](../firmware/air_control)에 있습니다.

```bash
cd ~/THIRDIMPACT_capstone/firmware/air_control
pio run -t upload
```

업로드 중에는 `fragrance_bridge`가 시리얼 포트를 잡고 있으므로 대시보드 서비스를 잠시 멈추세요.
