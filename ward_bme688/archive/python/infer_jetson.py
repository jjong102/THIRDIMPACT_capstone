#!/usr/bin/env python3
"""
infer_jetson.py  (v3)
Jetson 실시간 추론 노드.

=== v3 변경점: 실시간 Air 기준선 ===
모델은 baseline_correct.py 로 'Air 기준선을 뺀' 데이터로 학습되었다.
따라서 추론에서도 같은 차감을 해야 입력 분포가 일치한다.
(cycle 정규화를 해도 기준선 항은 사라지지 않는다:
   cycle_norm(x - b) = cycle_norm(x) - cycle_norm(b) )

실전에는 라벨이 없으므로 기준선을 스스로 유지한다:

  1) 부트스트랩 : 기동 후 BOOTSTRAP_CYCLES 만큼은 'Air 상태'로 가정하고 평균을 낸다.
                 → 반드시 발향을 끈 상태에서 기동할 것.
  2) 추종       : 이후 판정이 Air 이고 안정(stable)일 때만 지수이동평균으로 천천히 갱신.
                 → 센서 드리프트를 자동으로 따라간다.
  3) 보존       : 기준선을 파일에 저장. 재시작 시 불러와 부트스트랩을 건너뛴다.

센서별로 따로 관리한다(와드 단일 센서면 1개).

=== MQTT ===
  구독: scent/{device}/raw
        {"sensor":0, "step":3, "gas":123456.0, "temp":27.1, "hum":41.2}
  구독: scent/{device}/cmd
        "reset_baseline"  → 기준선을 버리고 부트스트랩 다시 (발향 끈 상태에서)
        "status"          → 현재 상태를 result 토픽으로 발행
  발행: scent/{device}/inference
        {"label_raw":..., "label_voted":..., "stable":true/false,
         "mixed":false, "proba":{...}, "baseline_ready":true}

=== 발향 제어 규칙 ===
  label_voted 와 stable=true 가 동시에 만족될 때만 액추에이터를 움직인다.
  baseline_ready=false 동안은 어떤 판정도 신뢰하지 말 것.

사용법:
  python3 infer_jetson.py --broker localhost --device ward01
"""

import argparse
import json
import os
import time
from collections import Counter, deque

import joblib
import numpy as np
import paho.mqtt.client as mqtt

N_STEPS = 10
N_SENSORS = 8                 # 와드 단일 센서면 1 로 두어도 되고, 그대로 둬도 동작
MIX_THRESHOLD = 0.65          # top1 이 이보다 낮고
MIX_GAP = 0.25                # top2 와 차이가 이보다 작으면 혼합 판정
VOTE_WINDOW = 5               # 다수결 창
AIR_LABEL = "Air"

BOOTSTRAP_CYCLES = 20         # 기동 후 이만큼을 Air 로 보고 기준선 생성
BASELINE_ALPHA = 0.02         # 기준선 추종 속도 (작을수록 느리고 안정적)
BASELINE_FILE = "baseline.json"


class Baseline:
    """센서별 Air 기준선을 유지한다."""

    def __init__(self, path=BASELINE_FILE):
        self.path = path
        self.vec = {}          # sensor -> np.array(10)
        self.boot = {}         # sensor -> [부트스트랩 중 모은 사이클들]
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            d = json.load(open(self.path))
            self.vec = {int(k): np.array(v, dtype=float) for k, v in d.items()}
            print(f"기준선 불러옴: 센서 {sorted(self.vec)} ({self.path})")
        except Exception as e:
            print("기준선 로드 실패:", e)

    def save(self):
        try:
            json.dump({str(k): v.tolist() for k, v in self.vec.items()},
                      open(self.path, "w"))
        except Exception as e:
            print("기준선 저장 실패:", e)

    def ready(self, sensor):
        return sensor in self.vec

    def get(self, sensor):
        return self.vec.get(sensor)

    def feed_bootstrap(self, sensor, log_gas):
        """부트스트랩 구간의 사이클을 모아 기준선을 만든다."""
        buf = self.boot.setdefault(sensor, [])
        buf.append(log_gas)
        if len(buf) >= BOOTSTRAP_CYCLES:
            self.vec[sensor] = np.mean(buf, axis=0)
            self.boot[sensor] = []
            self.save()
            print(f"[기준선] 센서 {sensor} 확립 ({BOOTSTRAP_CYCLES}사이클)")
            return True
        return False

    def track(self, sensor, log_gas):
        """Air 로 확정된 구간에서만 천천히 갱신 (드리프트 추종)."""
        if sensor not in self.vec:
            return
        self.vec[sensor] = ((1 - BASELINE_ALPHA) * self.vec[sensor]
                            + BASELINE_ALPHA * log_gas)

    def reset(self):
        self.vec, self.boot = {}, {}
        if os.path.exists(self.path):
            os.remove(self.path)
        print("[기준선] 초기화됨. 발향을 끈 상태로 두세요.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--broker", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--device", default="ward01")
    ap.add_argument("--model", default="scent_model.pkl")
    args = ap.parse_args()

    bundle = joblib.load(args.model)
    model = bundle["model"]
    normalize = bundle.get("normalize", "none")
    classes = model.classes_
    print(f"모델: {bundle.get('name','?')} / 정규화: {normalize} / 클래스 {list(classes)}")
    if AIR_LABEL not in classes:
        print(f"경고: '{AIR_LABEL}' 클래스가 없어 기준선 추종이 동작하지 않습니다")

    base = Baseline()
    buffers = {}                       # sensor -> {step: (gas, temp, hum)}
    cycle_probas = []                  # 이번 집계 윈도우의 센서별 확률
    last_log_gas = {}                  # sensor -> 마지막 사이클 (기준선 추종용)
    votes = deque(maxlen=VOTE_WINDOW)

    def featurize(log_gas, sensor):
        """학습과 동일한 변환: 기준선 차감 → 정규화"""
        x = log_gas - base.get(sensor)
        if normalize in ("cycle", "cycle_unit"):
            x = x - x.mean()
        if normalize in ("unit", "cycle_unit"):
            n = np.linalg.norm(x)
            if n > 0:
                x = x / n
        return x.reshape(1, -1)

    def publish(client):
        nonlocal cycle_probas
        proba = np.mean(cycle_probas, axis=0)
        order = np.argsort(proba)[::-1]
        top1, top2 = order[0], order[1]
        label_raw = str(classes[top1])
        mixed = bool(proba[top1] < MIX_THRESHOLD
                     and (proba[top1] - proba[top2]) < MIX_GAP)

        votes.append("Mixed" if mixed else label_raw)
        voted, n = Counter(votes).most_common(1)[0]
        stable = bool(len(votes) == VOTE_WINDOW and n > VOTE_WINDOW // 2)

        # Air 로 확정된 구간에서만 기준선을 천천히 따라가게 한다
        if stable and voted == AIR_LABEL:
            for s, lg in last_log_gas.items():
                base.track(s, lg)

        payload = {
            "label_raw": label_raw,
            "label_voted": voted,
            "stable": stable,
            "mixed": mixed,
            "proba": {str(c): round(float(p), 3) for c, p in zip(classes, proba)},
            "n_sensors": len(cycle_probas),
            "baseline_ready": True,
            "ts": time.time(),
        }
        client.publish(f"scent/{args.device}/inference", json.dumps(payload))
        print(f"{label_raw:>7} → {voted:<7}" + ("  [확정]" if stable else "  (수렴중)"))
        cycle_probas = []

    def on_message(client, userdata, msg):
        nonlocal cycle_probas
        try:
            if msg.topic.endswith("/cmd"):
                cmd = msg.payload.decode().strip()
                if cmd == "reset_baseline":
                    base.reset()
                    votes.clear()
                elif cmd == "status":
                    client.publish(
                        f"scent/{args.device}/inference",
                        json.dumps({"baseline_ready": bool(base.vec),
                                    "sensors": sorted(base.vec),
                                    "ts": time.time()}))
                return

            d = json.loads(msg.payload)
            s, step = int(d["sensor"]), int(d["step"])
            if float(d["gas"]) <= 0:
                return
            buffers.setdefault(s, {})[step] = (
                float(d["gas"]), float(d.get("temp", 0)), float(d.get("hum", 0)))

            if len(buffers[s]) < N_STEPS:
                return

            steps = buffers[s]
            buffers[s] = {}
            log_gas = np.log10(np.array([steps[i][0] for i in range(N_STEPS)]))
            last_log_gas[s] = log_gas

            # --- 기준선이 없으면 부트스트랩 ---
            if not base.ready(s):
                done = base.feed_bootstrap(s, log_gas)
                if not done:
                    left = BOOTSTRAP_CYCLES - len(base.boot.get(s, []))
                    if left % 5 == 0:
                        print(f"[기준선] 센서 {s} 부트스트랩 {left}사이클 남음"
                              " — 발향 금지")
                    client.publish(
                        f"scent/{args.device}/inference",
                        json.dumps({"baseline_ready": False,
                                    "bootstrap_left": left,
                                    "ts": time.time()}))
                return

            cycle_probas.append(model.predict_proba(featurize(log_gas, s))[0])
            if len(cycle_probas) >= min(N_SENSORS, max(1, len(last_log_gas))):
                publish(client)

        except Exception as e:
            print("처리 오류:", e)

    client = mqtt.Client()
    client.on_message = on_message
    client.connect(args.broker, args.port)
    client.subscribe(f"scent/{args.device}/raw")
    client.subscribe(f"scent/{args.device}/cmd")
    print(f"구독 시작: scent/{args.device}/raw")
    if not base.vec:
        print(f"기준선 없음 → 부트스트랩 {BOOTSTRAP_CYCLES}사이클."
              " 발향을 끈 상태로 두세요.")
    client.loop_forever()


if __name__ == "__main__":
    main()
