#!/usr/bin/env python3
"""
ward_controller.py
향 판정(open_set.py 모델)을 공기청정기 명령으로 바꾼다.

  판정            청정기
  Air             끔
  우리 향          끔   (가습기로 뿌린 향을 청정기가 빨아들이지 않게)
  Coffee(악취)     켬   (--on 으로 바꿀 수 있음, 모델의 on_labels 가 기본)
  Unknown         켬

입력은 capture_serial.py 가 쓰는 녹화 CSV 를 따라 읽는다 (live_view.py 와 같은 방식).
시리얼 포트를 직접 열지 않으므로 녹화·실시간 그래프와 함께 돌릴 수 있다.

출력은 Jetson 의 fragrance_bridge.py HTTP API 로 보낸다. (아두이노 포트는 브리지가 쥐고 있다)
  POST http://127.0.0.1:5174/api/fragrance/command   {"commands": ["ON000"]}
  ON000  = 청정기 켜기 + 가습 0초      OFF000 = 청정기 끄기 + 가습 0초
  ※ 두 명령 모두 가습 패턴을 0 으로 덮어쓴다. 다른 곳에서 향을 뿌리는 중이면 멈춘다.
  ※ 기본은 dry-run(명령을 화면에만 찍음). 실제로 보내려면 --send.

판단 규칙 (사이클 ≈ 9.7초)
  - 켜기: "켬" 판정이 --on-cycles 사이클(기본 3, 약 30초) 연속이면 ON000
  - 끄기: 켠 뒤 최소 --min-on 초(기본 180) 지나고, Air/우리 향이 --off-cycles(기본 12, 약 2분) 연속이면 OFF000
  - 기준선: 시작 후 평균선이 평평해지면(최근 1분 변동폭 < 0.02) 자동으로 잡는다. Air 여야 한다.
    이후 Air 가 계속되고 변화량이 작을 때만 아주 천천히 따라간다 (하루 동안의 드리프트 보정).
    향이 있을 때는 따라가지 않는다 — 따라가면 기준선이 향 쪽으로 끌려가 향을 못 보게 된다.

사용
  python ward_controller.py                    # 최근 녹화를 따라가며 dry-run (모델 기본 inference/openset_model.json)
  python ward_controller.py --send             # 실제 청정기 제어 (Jetson)
  python ward_controller.py --replay ..\\training\\recordings\\rec_....csv
                                                                       # 녹화 파일로 리허설
"""

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_view import Stream, Tail, newest_csv, N_STEPS            # noqa: E402
from paths import MODEL                                            # noqa: E402
from open_set import predict_one, vote, action_on, action_of, UNKNOWN, WEAK   # noqa: E402


class Controller:
    def __init__(self, model, args, send):
        self.model, self.args, self.send = model, args, send
        self.on_labels = args.on if args.on is not None else model.get("on_labels", ["Coffee"])
        self.st = Stream()
        self.pred = {}                    # 센서 -> 판정 (이번 사이클)
        self.purifier = False
        self.on_since = None
        self.run_on = self.run_off = 0    # 연속 사이클 수
        self.air_run = 0
        self.calm_run = 0                 # 청정기 꺼진 채 Air/Weak 가 이어진 사이클 수 (기준선 다시 잡기용)
        self.stuck_run = 0                # 청정기 켜진 채 켬 쪽 판정이 이어진 사이클 수 (갇힘 풀기용)
        self.lock_until = -1.0            # 이 시각(초)까지는 Unknown 으로 켜지 않음 (스스로 뿌린 향)
        self.rebases = []                 # 기준선을 다시 잡은 시각들
        self.log = []                     # (sec, 판정, k, n, |Δ|중앙, 청정기)

    # ---------- 스스로 뿌린 향 ----------
    def note_spray(self, sec, dur=0.0):
        """시스템이 향을 뿌렸다(뿌린다)고 알린다. 끝난 뒤 --spray-lock 초까지 Unknown 으로는 켜지 않는다.
        우리 향과 남은 악취가 섞이면 어떤 클래스와도 방향이 안 맞아 Unknown 이 되기 쉽다 —
        시스템은 자기가 뿌린 걸 알고 있으니 그 시간 동안은 '확실한 악취 클래스'로만 켠다."""
        if self.args.spray_lock > 0:
            self.lock_until = max(self.lock_until, sec + dur + self.args.spray_lock)

    # ---------- 기준선 ----------
    def _auto_baseline(self):
        res = self.st.groups()
        if res is None:
            return
        _, g = res
        now = g[-1, 0]
        if self.args.base_at is not None:          # 시점 지정 (리허설용)
            if now >= self.args.base_at:
                self.st.set_baseline(60)
                self._say(f"기준선 설정 ({self.args.base_at:.0f}초 지정) — 지금이 Air 여야 합니다")
            return
        m = g[:, 0] >= now - 60
        if now >= self.args.warmup and m.sum() >= 5 and np.ptp(g[m, 1]) < self.args.flat:
            self.st.set_baseline(60)
            self._say(f"기준선 자동 설정 (최근 1분 변동폭 {np.ptp(g[m, 1]):.3f}) — 지금이 Air 여야 합니다")

    def _track_baseline(self, mags):
        """Air 가 1분 이상 이어지고 변화량이 문턱의 절반 아래일 때만 기준선을 조금 따라간다."""
        if self.air_run < 6 or np.median(mags) > self.model["tau_air"] / 2:
            return
        a = self.args.track
        acc = {}
        for sec, s, k, x in self.st.recent:
            if sec >= self.st.sec_now - 60:
                acc.setdefault((s, k), []).append(x)
        for key, v in acc.items():
            if key in self.st.base:
                self.st.base[key] = (1 - a) * self.st.base[key] + a * (sum(v) / len(v))

    def _stuck_rebase(self, sec, want):
        """청정기가 켜진 채 '켬 쪽' 판정이 --stuck-rebase 초 동안 평평하게(변화 없이)이어지면 기준선을 다시 잡는다.
        실제 악취(양말 등)는 치우면 줄어들지만, 기준선이 어긋난 경우(예열 중에 잡힌 기준선이 흘러감)는
        세기가 일정하게 멈춰 '모르는 냄새 → 청정기 계속 켬' 에 갇힌다 (11/04 배터리 와드, |Δ| 0.38 로 15분).
        그대로 두면 다시 끌 방법이 없어서, 충분히 오래·평평할 때만 지금을 새 기준으로 삼는다."""
        if self.args.stuck_rebase <= 0 or not self.purifier or want is not True:
            self.stuck_run = 0
            return
        self.stuck_run += 1
        if self.stuck_run * 9.7 < self.args.stuck_rebase or sec - self.on_since < self.args.min_on:
            return
        res = self.st.groups()
        if res is None:
            return
        _, g = res
        m = g[:, 0] >= sec - self.args.stuck_rebase
        if m.sum() >= 5 and np.ptp(g[m, 1]) < self.args.flat:
            self.st.set_baseline(60)
            self.rebases.append(sec)
            self.stuck_run = 0
            self._say(f"기준선 다시 잡음 — 켜진 채 {self.args.stuck_rebase:.0f}초 동안 변화 없음 (기준선 어긋남으로 판단)")

    def _rebase(self, sec, top):
        """청정기가 꺼진 채 Air·Weak(보류)만 --rebase 초 이상 이어지고 그동안 평균선이 평평하면
        지금 상태를 새 기준선으로 잡는다. 센서 예열 끝무렵의 느린 상승, 남은 향, 하루 중 드리프트가
        조금씩 쌓여 '처음 보는 냄새'로 넘어가는 것을 막는다.
        우리 향·악취·Unknown 으로 판정되는 동안에는 잡지 않는다 (그 냄새를 기준으로 삼으면 안 되므로)."""
        if self.args.rebase <= 0:
            return
        calm = (self.model["air_label"],) + ((WEAK,) if self.args.rebase_weak else ())
        if self.purifier or top not in calm or sec < self.lock_until:
            self.calm_run = 0
            return
        self.calm_run += 1
        if self.calm_run * 9.7 < self.args.rebase:
            return
        res = self.st.groups()
        if res is None:
            return
        _, g = res
        m = g[:, 0] >= sec - self.args.rebase
        if m.sum() >= 5 and np.ptp(g[m, 1]) < self.args.flat:
            self.st.set_baseline(60)
            self.rebases.append(sec)
            self.calm_run = 0
            self._say(f"기준선 다시 잡음 (최근 {self.args.rebase:.0f}초 평평, {top})")

    # ---------- 판정 ----------
    def feed(self, line):
        self.st.feed(line)
        if self.st.base is None:
            if self.st.done and self.st.done[-1][0] > self.args.warmup:
                self._auto_baseline()
            self.st.done.clear()
            return
        while self.st.done:
            sec, s, vec = self.st.done.popleft()
            base = [self.st.base.get((s, k)) for k in range(N_STEPS)]
            if any(b is None for b in base):
                continue
            d = np.array(vec) - np.array(base)
            lab, info = predict_one(self.model, d)
            self.pred[s] = (lab, info.get("mag", np.nan))
            if len(self.pred) >= self.n_sensors():
                self._decide(sec)

    def n_sensors(self):
        return max(len(self.st.last), 1)

    def _decide(self, sec):
        labs = [l for l, _ in self.pred.values()]
        mags = [m for _, m in self.pred.values()]
        self.pred = {}
        top, k, n = vote(labs, self.on_labels)
        want = action_of(top, self.on_labels)
        if want and top == UNKNOWN and sec < self.lock_until:
            want = None                      # 스스로 뿌린 향 직후: Unknown 은 보류로 취급
        self.air_run = self.air_run + 1 if top == self.model["air_label"] else 0
        if want is None:                     # Weak(보류): 연속 카운트를 건드리지 않는다
            pass
        elif want:
            self.run_on, self.run_off = self.run_on + 1, 0
        else:
            self.run_on, self.run_off = 0, self.run_off + 1
        if not self.purifier and self.run_on >= self.args.on_cycles:
            self._command(self.args.on_cmd, sec, f"{top} {self.run_on}사이클 연속")
            self.purifier, self.on_since = True, sec
            if self.args.on_cmd[2:].strip("0"):          # ON090 처럼 출동하면서 향도 뿌리면
                self.lock_until = float("inf")           # 뿌리는 동안 잠금, 끌 때 note_spray 로 풀림 시각 설정
        elif (self.purifier and self.run_off >= self.args.off_cycles
              and sec - self.on_since >= self.args.min_on):
            self._command("OFF000", sec, f"{top} {self.run_off}사이클 연속")
            self.purifier = False
            if self.lock_until == float("inf"):          # 출동 중 뿌린 향이 멈춤 → 그때부터 잠금 시간
                self.lock_until = -1.0
                self.note_spray(sec)
        self._track_baseline(mags)
        self._rebase(sec, top)
        self._stuck_rebase(sec, want)
        self.log.append((sec, top, k, n, float(np.median(mags)), self.purifier))
        if self.args.verbose:
            print(f"  {sec/60:6.1f}분  {top:10s} {k}/{n}  |Δ| {np.median(mags):5.2f}"
                  f"   청정기 {'ON' if self.purifier else 'off'}")

    # ---------- 출력 ----------
    def _say(self, msg):
        print(f"[{self.st.sec_now/60:6.1f}분] {msg}", flush=True)

    def _command(self, cmd, sec, why):
        self._say(f"청정기 {'ON ' if cmd.startswith('ON') else 'OFF'} ← {why}   명령 {cmd}"
                  + ("" if self.send else "  (dry-run)"))
        if not self.send:
            return
        body = json.dumps({"commands": [cmd]}).encode("utf-8")
        req = urllib.request.Request(self.args.bridge, data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                res = json.loads(r.read().decode("utf-8"))
            if not res.get("sent"):
                self._say(f"  ⚠ 브리지가 아두이노에 못 보냄: {res.get('replies')}")
        except Exception as e:
            self._say(f"  ⚠ 브리지 연결 실패: {e}")


def summarize(c):
    if not c.log:
        print("판정이 하나도 없습니다 (기준선이 안 잡혔을 수 있음 — 녹화 초반이 평평한지 확인).")
        return
    on_cnt = sum(1 for i in range(1, len(c.log)) if c.log[i][5] and not c.log[i - 1][5])
    print(f"\n=== 판정 흐름 (바뀐 지점만) ===   사이클 {len(c.log)}개, "
          f"{c.log[0][0]/60:.1f}~{c.log[-1][0]/60:.1f}분, 청정기 켠 횟수 {on_cnt}")
    prev = None
    for sec, top, k, n, mag, on in c.log:
        key = (top, on)
        if key != prev:
            print(f"  {sec/60:6.1f}분  {top:10s} {k}/{n}  |Δ| {mag:5.2f}   청정기 {'ON' if on else 'off'}")
            prev = key


def main():
    ap = argparse.ArgumentParser(description="향 판정 → 공기청정기 제어")
    ap.add_argument("--model", type=Path, default=MODEL, help="기본 inference/openset_model.json")
    ap.add_argument("csv", nargs="?", type=Path, help="따라갈 녹화 CSV (생략하면 가장 최근)")
    ap.add_argument("--replay", type=Path, help="녹화 파일을 처음부터 끝까지 빠르게 재생 (리허설)")
    ap.add_argument("--port", help="시리얼(예: COM5)을 직접 읽어 판정만 함 — 녹화·그래프 없이 추론만 볼 때")
    ap.add_argument("--send", action="store_true", help="실제로 브리지에 명령 전송 (기본 dry-run)")
    ap.add_argument("--bridge", default="http://127.0.0.1:5174/api/fragrance/command")
    ap.add_argument("--on", nargs="*", default=None, help="청정기를 켤 향 라벨 (기본: 모델 설정)")
    ap.add_argument("--on-cycles", type=int, default=3)
    ap.add_argument("--off-cycles", type=int, default=12)
    ap.add_argument("--min-on", type=float, default=180.0)
    ap.add_argument("--warmup", type=float, default=60.0, help="이 시간(초) 전에는 기준선을 잡지 않음")
    ap.add_argument("--flat", type=float, default=0.05,
                    help="자동 기준선 조건: 8센서 평균선의 최근 1분 변동폭이 이 값 미만 "
                         "(습도 출렁임이 0.02~0.05 라 기본 0.05)")
    ap.add_argument("--base-at", type=float, default=None,
                    help="자동 대신 이 시각(초, 녹화 시작 기준)에 기준선을 잡음. 리허설용")
    ap.add_argument("--track", type=float, default=0.02, help="기준선 추종 비율 (0 이면 끔)")
    ap.add_argument("--rebase", type=float, default=180.0,
                    help="청정기 꺼진 채 Air/보류가 이 시간(초) 평평하게 이어지면 기준선을 다시 잡음 (0 이면 끔)")
    ap.add_argument("--stuck-rebase", type=float, default=300.0,
                    help="청정기 켜진 채 켬 쪽 판정이 이 시간(초) 평평하게 이어지면 기준선을 다시 잡음 (0 이면 끔)")
    ap.add_argument("--rebase-weak", action="store_true",
                    help="보류(Weak) 상태에서도 기준선을 다시 잡음 (기본은 Air 일 때만)")
    ap.add_argument("--spray-lock", type=float, default=180.0,
                    help="스스로 향을 뿌린 뒤 이 시간(초) 동안 Unknown 으로는 켜지 않음 (0 이면 끔)")
    ap.add_argument("--on-cmd", default="ON000",
                    help="출동 명령. 출동하면서 향도 뿌리려면 예: ON090 (라벤더). 향을 뿌리면 --spray-lock 적용")
    ap.add_argument("-v", "--verbose", action="store_true", help="사이클마다 판정 출력")
    args = ap.parse_args()

    model = json.loads(args.model.read_text(encoding="utf-8"))
    print(f"[모델] {args.model}  향: {', '.join(model['classes']) or '없음'}  "
          f"Air 문턱 {model['tau_air']:.2f}")
    c = Controller(model, args, send=args.send and not args.replay)
    print(f"[규칙] 켬 = {', '.join(c.on_labels + [UNKNOWN])} {args.on_cycles}사이클 연속 / "
          f"끔 = 나머지 {args.off_cycles}사이클 연속 (켠 뒤 최소 {args.min_on:.0f}초)")

    if args.replay:
        for ln in args.replay.read_text(encoding="utf-8", errors="replace").splitlines():
            c.feed(ln.strip())
        summarize(c)
        return

    if args.port:                        # 시리얼을 직접 읽어 판정만 (녹화·그래프 없음)
        import serial
        print(f"[입력] {args.port} 직접 ({'실제 전송' if args.send else 'dry-run'})  녹화 안 함 · Ctrl+C 로 종료\n"
              "  기준선은 켠 지 1분 지나고 평균선이 1분 평평해지면 자동으로 잡힌다 (보통 2~5분)\n")
        ser = serial.Serial(args.port, 115200, timeout=1)
        last_note = time.time()
        try:
            while True:
                raw = ser.readline()
                if raw:
                    ln = raw.decode("utf-8", errors="replace").strip()
                    if ln.startswith("# I ") or "Brownout" in ln or ln.startswith("rst:"):
                        print(f"  [와드] {ln[2:] if ln.startswith('# ') else ln}")   # 와드 온디바이스 판정도 같이 보여 줌
                    c.feed(ln)
                if c.st.base is None and time.time() - last_note > 30:
                    print(f"  … 예열 중 ({c.st.sec_now/60:.1f}분, 기준선 기다림)")
                    last_note = time.time()
        except KeyboardInterrupt:
            summarize(c)
        return

    path = args.csv or newest_csv()
    if path is None:
        sys.exit("따라갈 녹화 CSV 가 없습니다. capture_serial.py 로 녹화를 시작하세요.")
    print(f"[입력] {path}   ({'실제 전송' if args.send else 'dry-run'})  Ctrl+C 로 종료\n")
    tail = Tail(path)
    try:
        while True:
            for ln in tail.poll():
                c.feed(ln)
            time.sleep(1.0)
    except KeyboardInterrupt:
        summarize(c)


if __name__ == "__main__":
    main()
