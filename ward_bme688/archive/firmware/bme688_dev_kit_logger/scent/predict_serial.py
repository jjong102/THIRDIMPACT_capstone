#!/usr/bin/env python3
"""
predict_serial.py
bme688_serial_stream.ino 가 보내는 시리얼 데이터를 읽어 실시간으로 향을 판정한다.
WiFi / MQTT / SD 카드 없이 USB 케이블 하나로 동작한다.

=== 처리 (학습 파이프라인과 동일) ===
  1. 기준선   : 'Air' 상태에서 센서별 평균 지문을 만든다.
                단순히 개수만 채우지 않고, 전반부와 후반부의 지문이
                STABLE_TH 이내로 일치할 때까지 기다린다.
                → 환기가 덜 됐거나 예열 중이면 자동으로 더 기다린다
                → 이 동안 반드시 발향을 끄고 있을 것
  2. 특징     : log10(가스저항) - 기준선  →  모델에 저장된 정규화 방식 적용
  3. 집계     : 8센서 확률 평균 → 확정 판정
                - 확신도가 CONF_INSTANT 를 넘으면 즉시 확정 (약 11초)
                - 애매하면 최근 VOTE 회 다수결로 확정 (약 23초)
                → 확실한 향은 빠르게, 애매한 향은 신중하게 판단한다
  4. 추종     : 판정이 Air 로 확정된 구간에서만 기준선을 천천히 갱신
                → 센서 드리프트를 따라가므로 오래 켜둬도 무너지지 않는다

=== 준비 ===
  pip install pyserial joblib scikit-learn numpy
  ※ 아두이노 시리얼 모니터는 반드시 닫을 것 (포트를 한 프로그램만 열 수 있음)

=== 사용법 ===
  python predict_serial.py --port COM5
  python predict_serial.py --port COM5 --reset      # 저장된 기준선 버리고 다시
  python predict_serial.py --port COM5 --raw        # 판정 대신 원시값 확인용
  python predict_serial.py --port COM5 --vote 1     # 투표 끄기 (가장 빠름, 노이즈 있음)
  python predict_serial.py --port COM5 --conf 1.1   # 즉시확정 끄기 (가장 신중)

  # 원인 분석용 기록 (오분류가 날 때)
  python predict_serial.py --port COM5 --log run.csv --mark Floral

  종료는 Ctrl+C
"""

import argparse
import json
import os
import sys
import time
from collections import Counter, deque

import warnings

import joblib
import numpy as np

# 다른 sklearn 버전으로 저장된 모델을 열 때 나오는 경고 (동작에는 영향 없음)
warnings.filterwarnings("ignore", message=".*InconsistentVersion.*")
try:
    from sklearn.exceptions import InconsistentVersionWarning
    warnings.filterwarnings("ignore", category=InconsistentVersionWarning)
except ImportError:
    pass

try:
    import serial
except ImportError:
    sys.exit("pyserial 이 필요합니다:  pip install pyserial")

N_STEPS = 10
BOOTSTRAP = 10          # 기준선 확립에 쓸 최소 사이클 수 (센서당)
STABLE_TH = 0.030       # 기준선 안정 판정 기준.
                        # 버퍼 전반부와 후반부의 지문 차이가 이보다 작아야 확정.
                        # 학습 시 향 신호가 0.168~0.440 이므로 0.03 이면 충분히 안전.
MAX_BOOT = 60           # 이만큼 모아도 안정 안 되면 경고 후 강행
                        # 사이클이 약 11초이므로 센서당 10개 ≈ 2분
VOTE = 3                # 다수결 창 (5 → 3 으로 단축)
CONF_INSTANT = 0.85     # 이 확신도를 넘으면 투표를 기다리지 않고 즉시 확정
EMIT_TIMEOUT = 3.0      # 센서 일부가 누락돼도 이 시간이 지나면 집계 진행
ALPHA = 0.15            # 기준선 추종 속도.
                        # 실측: 0.02 는 한 Air 블록에서 26%만 따라가 기준선이 낡고
                        # Floral 을 Air 로 오판했다. 0.15 면 한 블록에 90% 수렴.
MIX_TH, MIX_GAP = 0.65, 0.25
AIR = "Air"
BASE_FILE = "baseline_serial.json"

BAR = "█"


def load_baseline(path):
    if not os.path.exists(path):
        return {}
    try:
        d = json.load(open(path))
        return {int(k): np.array(v, dtype=float) for k, v in d.items()}
    except Exception:
        return {}


def save_baseline(path, base):
    try:
        json.dump({str(k): v.tolist() for k, v in base.items()}, open(path, "w"))
    except Exception as e:
        print("기준선 저장 실패:", e)


def featurize(log_gas, base_vec, normalize):
    x = log_gas - base_vec
    if normalize in ("cycle", "cycle_unit"):
        x = x - x.mean()
    if normalize in ("unit", "cycle_unit"):
        n = np.linalg.norm(x)
        if n > 0:
            x = x / n
    return x.reshape(1, -1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True, help="예: COM5 또는 /dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--model", default="scent_model.pkl")
    ap.add_argument("--reset", action="store_true", help="저장된 기준선 삭제")
    ap.add_argument("--raw", action="store_true", help="원시값만 출력 (진단용)")
    ap.add_argument("--vote", type=int, default=VOTE, help=f"다수결 창 (기본 {VOTE})")
    ap.add_argument("--conf", type=float, default=CONF_INSTANT,
                    help=f"즉시 확정 확신도 (기본 {CONF_INSTANT}, 1.1 이면 비활성)")
    ap.add_argument("--alpha", type=float, default=ALPHA,
                    help=f"기준선 추종 속도 (기본 {ALPHA})")
    ap.add_argument("--log", metavar="FILE",
                    help="사이클과 판정을 CSV로 저장 (원인 분석용). "
                         "학습 데이터와 직접 비교할 수 있다")
    ap.add_argument("--mark", metavar="LABEL",
                    help="로그에 붙일 실제 향 이름 (예: --mark Floral). "
                         "나중에 정답과 대조할 때 쓴다")
    args = ap.parse_args()

    bundle = joblib.load(args.model)
    model = bundle["model"]
    normalize = bundle.get("normalize", "none")
    classes = list(model.classes_)
    print(f"모델: {bundle.get('name','?')} / 정규화: {normalize}")
    print(f"클래스: {classes}\n")

    if args.reset and os.path.exists(BASE_FILE):
        os.remove(BASE_FILE)
        print("저장된 기준선을 삭제했습니다.")

    base = load_baseline(BASE_FILE)
    boot = {}
    if base:
        print(f"기준선 불러옴 (센서 {sorted(base)}). 다시 잡으려면 --reset\n")
    else:
        print(f"기준선 없음 → 센서당 {BOOTSTRAP}사이클 수집.")
        print("★ 이 동안 발향을 켜지 마세요 ★\n")

    logf = None
    if args.log:
        new = not os.path.exists(args.log)
        logf = open(args.log, "a", encoding="utf-8", newline="")
        if new:
            cols = ",".join(f"log_gas_{i}" for i in range(N_STEPS))
            logf.write("time_s,sensor,mark,pred,voted,stable,conf,"
                       + cols + ","
                       + ",".join(f"base_{i}" for i in range(N_STEPS))
                       + ",temp,hum\n")
        print(f"기록: {args.log} (mark={args.mark or '-'})")

    ser = serial.Serial(args.port, args.baud, timeout=1)
    time.sleep(2)
    ser.reset_input_buffer()
    print(f"{args.port} 연결됨. Ctrl+C 로 종료.\n")

    buffers = {}            # sensor -> {step: gas}
    last_cycle = {}         # sensor -> 마지막 사이클 (추종용)
    probas = []             # 이번 집계 윈도우
    t_first = None          # 집계 윈도우의 첫 사이클 시각
    votes = deque(maxlen=max(1, args.vote))
    last_th = (0.0, 0.0)
    n_cycles = 0
    t_start = time.time()
    max_step = 0            # 진행률 표시용
    pending = []            # 이번 집계에 들어간 (센서, 사이클, 기준선)
    last_draw = 0.0
    cur_line = ""           # 마지막으로 그린 판정 줄

    try:
        while True:
            line = ser.readline().decode("utf-8", "ignore").strip()
            if not line:
                continue
            if line.startswith("#"):
                print(line)
                continue
            if not line.startswith("D,"):
                continue

            p = line.split(",")
            if len(p) < 6:
                continue
            try:
                s, step = int(p[1]), int(p[2])
                gas, temp, hum = float(p[3]), float(p[4]), float(p[5])
            except ValueError:
                continue
            if gas <= 0 or not (0 <= step < N_STEPS):
                continue

            last_th = (temp, hum)
            buffers.setdefault(s, {})[step] = gas

            # --- 사이클 진행률 (화면이 멈춰 보이지 않게) ---
            if base and not args.raw:
                max_step = max(max_step, step)
                now = time.time()
                if now - last_draw > 0.3:
                    last_draw = now
                    prog = "▓" * (max_step + 1) + "░" * (N_STEPS - max_step - 1)
                    print(f"\r{cur_line}  [{prog}]", end="", flush=True)

            if len(buffers[s]) < N_STEPS:
                continue

            steps = buffers[s]
            buffers[s] = {}
            log_gas = np.log10(np.array([steps[i] for i in range(N_STEPS)]))
            last_cycle[s] = log_gas
            n_cycles += 1

            if args.raw:
                print(f"S{s} " + " ".join(f"{v:.2f}" for v in log_gas))
                continue

            # ---------- 기준선 부트스트랩 ----------
            if s not in base:
                buf = boot.setdefault(s, [])
                buf.append(log_gas)
                if len(buf) > MAX_BOOT:
                    buf.pop(0)

                drift = None
                if len(buf) >= BOOTSTRAP:
                    # 전반부 / 후반부 지문을 비교해 아직 흐르고 있는지 본다
                    h = len(buf) // 2
                    a = np.mean(buf[:h], axis=0); a = a - a.mean()
                    b2 = np.mean(buf[h:], axis=0); b2 = b2 - b2.mean()
                    drift = float(np.linalg.norm(a - b2))

                    forced = len(buf) >= MAX_BOOT
                    if drift <= STABLE_TH or forced:
                        base[s] = np.mean(buf[h:], axis=0)   # 최근 절반만 사용
                        boot[s] = []
                        save_baseline(BASE_FILE, base)
                        tag = "" if not forced else "  << 불안정한 채로 강행"
                        print(f"\r[기준선] 센서 {s} 확립 (변동 {drift:.3f})"
                              f"  {len(base)}/8{tag}" + " " * 20)
                        if len(base) == 8:
                            print("\n기준선 완료. 이제 향을 넣어보세요.\n")
                        continue

                got = min(len(v) for v in boot.values()) if boot else 0
                dtxt = f"변동 {drift:.3f} (목표 {STABLE_TH})" if drift is not None \
                    else f"{got}/{BOOTSTRAP} 사이클"
                print(f"\r기준선 안정 대기  {dtxt}"
                      f"  (센서 {len(base)}/8)  — ★ 발향 금지 ★   ",
                      end="", flush=True)
                continue

            # ---------- 예측 ----------
            probas.append(model.predict_proba(
                featurize(log_gas, base[s], normalize))[0])
            pending.append((s, log_gas, base[s].copy()))
            if t_first is None:
                t_first = time.time()

            # 센서가 다 모이거나, 일부가 누락돼도 타임아웃이면 진행
            enough = len(probas) >= max(1, len(base))
            timeout = (time.time() - t_first) > EMIT_TIMEOUT
            if not (enough or timeout):
                continue

            m = np.mean(probas, axis=0)
            n_used = len(probas)
            probas = []
            t_first = None
            max_step = 0
            batch, pending = pending, []

            # ---------- 신호 세기 (표시/경고용) ----------
            sig_mag = 0.0
            if batch:
                dv = np.mean([lg - bv for _, lg, bv in batch], axis=0)
                sig_mag = float(np.linalg.norm(dv - dv.mean()))
            order = np.argsort(m)[::-1]
            top1, top2 = order[0], order[1]
            raw = classes[top1]
            mixed = bool(m[top1] < MIX_TH and (m[top1] - m[top2]) < MIX_GAP)

            votes.append("Mixed" if mixed else raw)
            voted, cnt = Counter(votes).most_common(1)[0]

            conf = float(m[top1])
            weak = " [신호약함]" if sig_mag < 0.05 else ""
            by_vote = len(votes) == votes.maxlen and cnt > votes.maxlen // 2
            by_conf = (not mixed) and conf >= args.conf     # 확신하면 즉시 확정

            if by_conf:
                voted = raw                                  # 최신 판정을 바로 채택
            stable = bool(by_vote or by_conf)
            how = "즉시" if by_conf else ("투표" if by_vote else "")

            # Air 로 확정된 구간에서만 기준선을 천천히 따라간다
            if stable and voted == AIR:
                for k, v in last_cycle.items():
                    if k in base:
                        base[k] = (1 - args.alpha) * base[k] + args.alpha * v

            # ---------- 출력 ----------
            elapsed = int(time.time() - t_start)
            bars = " ".join(f"{c[:6]:>6} {BAR * int(p * 12):<10}"
                            for c, p in zip(classes, m))
            flag = f"확정({how})" if stable else "수렴중  "
            mix = " [혼합]" if mixed else ""
            warn = "" if n_used >= len(base) else f" (센서{n_used})"
            cur_line = (f"{elapsed//60:02d}:{elapsed%60:02d} "
                        f"| {voted:<7} {flag}{mix}{warn} "
                        f"| S{sig_mag:.2f}{weak} "
                        f"| {last_th[0]:.1f}C {last_th[1]:.0f}% | {bars}")
            print(f"\r{cur_line}" + " " * 14, end="", flush=True)

            # ---------- 로그 기록 ----------
            if logf:
                for sn, lg, bv in batch:
                    logf.write(f"{elapsed},{sn},{args.mark or ''},{raw},"
                               f"{voted},{int(stable)},{conf:.4f},"
                               + ",".join(f"{v:.5f}" for v in lg) + ","
                               + ",".join(f"{v:.5f}" for v in bv) + ","
                               + f"{last_th[0]:.2f},{last_th[1]:.2f}\n")
                logf.flush()

    except KeyboardInterrupt:
        print("\n\n종료.")
        if base:
            save_baseline(BASE_FILE, base)
            print(f"기준선 저장됨 ({BASE_FILE}) — 다음 실행 때 재사용됩니다.")
        print(f"총 {n_cycles} 사이클 처리")
    finally:
        ser.close()
        if logf:
            logf.close()
            print(f"기록 저장됨: {args.log}")


if __name__ == "__main__":
    main()
