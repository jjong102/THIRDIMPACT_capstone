#!/usr/bin/env python3
"""
live_view.py
녹화 중인 CSV 를 따라가며(tail) 실시간 그래프로 보여준다.

  위     8센서 평균 (히터 사이클 1회 = 점 1개). label_tool.py 와 같은 값.
  (--humidity) 칩 습도. 평균선이 습도와 같이 움직이면 가스가 아니라 방 환경 변화다.
         판정에는 습도를 안 쓰므로 기본은 숨김. 분무 점검(1분 분무에 +2% 이상)할 때만 켠다.
  아래   기준선 대비 스텝별 변화 Δ 와 |Δ|. 향 농도를 맞출 때 쓴다.
         스텝이 도착하는 대로 갱신되므로 사이클이 끝나길 기다리지 않는다.

키:  b = 지금(최근 60초)을 기준선(Air)으로   c = 기준선 해제   q = 종료

왜 파일을 읽나
  시리얼 포트는 한 프로그램만 열 수 있다. capture_serial.py 가 포트를 잡고 CSV 를 쓰는
  동안 이 도구는 그 CSV 를 읽기만 하므로 녹화에 영향이 없다. 창을 닫아도 녹화는 계속된다.
  (capture_serial.py 는 기본으로 이 창을 자동으로 띄운다. --no-live 로 끔)

사용:
  python live_view.py                          # training/recordings/ 아래 가장 최근 CSV
  python live_view.py ../training/recordings/rec_....csv
  python live_view.py --window 0               # 전체 구간 (기본은 최근 10분)
  python live_view.py --humidity               # 습도 그래프도 표시 (분무 점검용)

의존성: numpy, matplotlib  (스텝 온도 라벨을 위해 parse_devkit_csv.py 를 import)
"""

import argparse
import math
import sys
import time
import warnings
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass
warnings.simplefilter("ignore", RuntimeWarning)      # 빈 구간 nanmean 경고

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse_devkit_csv import STEP_TEMPS              # 히터 온도표의 단일 출처

for _f in ("Malgun Gothic", "AppleGothic", "NanumGothic"):
    if any(_f == f.name for f in matplotlib.font_manager.fontManager.ttflist):
        matplotlib.rcParams["font.family"] = _f
        break
matplotlib.rcParams["axes.unicode_minus"] = False
plt.rcParams["keymap.back"] = ["left", "backspace"]   # 'c' 를 기준선 해제에 쓰려고 비움

N_STEPS = 10


C_DOWN, C_UP, C_MUTED = "#B2441C", "#0E7A69", "#6B7673"


def _mean(v):
    v = [a for a in v if a == a]
    return sum(v) / len(v) if v else float("nan")


class Tail:
    """CSV 를 읽을 때만 열고 바로 닫는다. (열어 두면 Windows 에서 녹화 종료 시 이름 변경이 막힌다)"""

    def __init__(self, path):
        self.path, self.pos, self.buf = Path(path), 0, b""

    def poll(self):
        try:
            size = self.path.stat().st_size
            if size < self.pos:                   # 파일이 새로 쓰였다
                self.pos, self.buf = 0, b""
            if size == self.pos:
                return []
            with open(self.path, "rb") as f:
                f.seek(self.pos)
                data = f.read(size - self.pos)
        except OSError:
            return []
        self.pos += len(data)
        parts = (self.buf + data).split(b"\n")
        self.buf = parts.pop()                    # 마지막 줄은 아직 덜 써졌을 수 있다
        return [b.decode("utf-8", "replace").strip() for b in parts]


class Stream:
    """CSV 줄을 받아 유효 측정만 모으고, 센서별로 10스텝 사이클을 조립한다."""

    def __init__(self):
        self.t0 = None
        self.last_t = None
        self.sec_now = 0.0
        self.n_rows = 0
        self.resets = 0
        self.cyc = {}                         # 센서 -> 조립 중인 사이클
        self.cycles = []                      # (sec, sensor, 10스텝 평균 log, 습도, 온도)
        self.last = defaultdict(dict)         # 센서 -> 스텝 -> (sec, log) 가장 최근 값
        self.done = deque(maxlen=400)         # 끝난 사이클 (sec, sensor, 10스텝 log) — 판정용
        self.recent = deque()                 # 기준선 계산용 최근 150초
        self.base = None                      # (센서, 스텝) -> 기준 log
        self.base_avg = None
        self.base_sec = None

    def feed(self, line):
        if not line or line[0] == "#" or line.startswith("TimeStamp"):
            return
        p = line.split(",")
        if len(p) < 12:
            return
        try:
            t, s = float(p[0]), int(p[1])
        except ValueError:
            return
        if self.t0 is None:
            self.t0 = t
        elif t < self.last_t - 1000:          # 보드가 리셋돼 TimeStamp 가 0부터 다시 시작
            self.t0 += t - self.last_t
            self.cyc.clear()
            self.resets += 1
        self.last_t = t
        sec = (t - self.t0) / 1000.0
        self.sec_now = sec
        self.n_rows += 1
        try:
            g, k, valid = float(p[5]), int(p[6]), int(float(p[10]))
        except ValueError:
            return
        if valid == 0 or g <= 0 or not (0 <= k < N_STEPS):
            return                            # 더미 슬롯은 측정이 아니다
        x = math.log10(g)
        try:
            h, tc = float(p[4]), float(p[2])
        except ValueError:
            h = tc = float("nan")

        c = self.cyc.get(s)
        if c is None:
            c = self.cyc[s] = {"prev": -1, "steps": {}, "t": [], "h": [], "tc": []}
        if k < c["prev"]:                     # 스텝 번호가 되감김 = 이전 사이클 끝
            if len(c["steps"]) == N_STEPS:    # 10스텝이 다 모인 완전한 사이클만
                self.cycles.append((_mean(c["t"]), s, _mean(list(c["steps"].values())),
                                    _mean(c["h"]), _mean(c["tc"])))
                self.done.append((_mean(c["t"]), s, [c["steps"][i] for i in range(N_STEPS)]))
            c.update(steps={}, t=[], h=[], tc=[])
        c["prev"] = k
        c["steps"][k] = x
        c["t"].append(sec)
        c["h"].append(h)
        c["tc"].append(tc)

        self.last[s][k] = (sec, x)
        self.recent.append((sec, s, k, x))
        while self.recent and self.recent[0][0] < sec - 150:
            self.recent.popleft()

    def groups(self):
        """같은 히터 사이클의 센서들을 묶어 점 1개로. 센서가 다 모인 묶음만 쓴다.
        (일부 센서만 평균하면 센서 개체차 때문에 값이 튄다)

        센서 8개의 사이클은 한 주기 안에서 최대 7초쯤 엇갈려 끝난다(실측 S1 6.1초 … S2 13.1초).
        그래서 시간 간격으로는 못 묶고, 센서마다 자기 사이클 순번 k 를 센 뒤 같은 k 끼리 묶는다.
          - 주기 P = 센서별 연속 사이클 간격의 중앙값
          - 기준 위상 = 센서들 첫 사이클 위상(mod P) 사이의 가장 큰 빈틈 한가운데
          - 첫 사이클 k = floor((시각 - 기준) / P), 이후 k += round(간격 / P)  (빠진 사이클도 셈)
        순번을 이어 세므로 긴 녹화에서 주기 추정 오차가 쌓여도 묶음이 어긋나지 않는다."""
        if len(self.cycles) < 4:
            return None
        a = np.array(self.cycles, dtype=float)
        by_s = {}
        for row in a[np.argsort(a[:, 0])]:
            by_s.setdefault(int(row[1]), []).append(row)
        diffs = [np.diff([r[0] for r in rows]) for rows in by_s.values() if len(rows) >= 2]
        diffs = np.concatenate(diffs) if diffs else np.array([])
        if len(diffs) == 0:
            return None
        P = float(np.median(diffs))
        if not P > 0:
            return None

        firsts = {s: rows[0][0] for s, rows in by_s.items()}
        ph = np.sort(np.mod(list(firsts.values()), P))
        gaps = np.diff(np.append(ph, ph[0] + P))
        i = int(gaps.argmax())
        anchor = ph[i] + gaps[i] / 2.0

        acc = defaultdict(lambda: defaultdict(list))   # k -> 센서 -> [(avg, 습도, 온도)]
        for s, rows in by_s.items():
            k = int(math.floor((rows[0][0] - anchor) / P))
            prev = rows[0][0]
            for j, r in enumerate(rows):
                if j:
                    k += max(1, int(round((r[0] - prev) / P)))
                    prev = r[0]
                acc[k][s].append((r[0], r[2], r[3], r[4]))
        ks = sorted(acc)
        counts = [len(acc[k]) for k in ks]
        if len(ks) < 2:
            return None
        n_active = int(np.bincount(counts[:-1]).argmax())   # 진행 중인 마지막 묶음은 빼고 셈
        out = []
        for k in ks:
            if len(acc[k]) != n_active:
                continue
            per = np.array([np.mean(v, axis=0) for v in acc[k].values()])
            out.append([per[:, 0].mean(), per[:, 1].mean(),
                        np.nanmean(per[:, 2]), np.nanmean(per[:, 3])])
        if not out:
            return None
        return n_active, np.array(out)        # 열: 시각(초), 평균 log, 습도, 온도

    def set_baseline(self, win=60.0):
        acc = defaultdict(list)
        for sec, s, k, x in self.recent:
            if sec >= self.sec_now - win:
                acc[(s, k)].append(x)
        if not acc:
            return False
        self.base = {key: sum(v) / len(v) for key, v in acc.items()}
        self.base_avg = sum(self.base.values()) / len(self.base)
        self.base_sec = self.sec_now
        return True

    def clear_baseline(self):
        self.base = self.base_avg = self.base_sec = None

    def delta(self):
        """스텝별 Δ = 센서별 (최근값 - 기준값) 의 센서 평균."""
        if self.base is None:
            return None
        per = [[] for _ in range(N_STEPS)]
        for s, d in self.last.items():
            for k, (_, x) in d.items():
                b = self.base.get((s, k))
                if b is not None:
                    per[k].append(x - b)
        return np.array([sum(v) / len(v) if v else np.nan for v in per])


def _set_ylim(ax, vals, extra=(), min_span=0.06, pad=0.12):
    v = np.append(np.asarray(vals, dtype=float), list(extra))
    v = v[np.isfinite(v)]
    if not len(v):
        return
    lo, hi = v.min(), v.max()
    span = max(hi - lo, min_span)
    mid = (hi + lo) / 2
    ax.set_ylim(mid - span * (0.5 + pad), mid + span * (0.5 + pad))


class View:
    def __init__(self, path, args):
        self.args = args
        self.path = Path(path)
        self.tail = Tail(path)
        self.st = Stream()
        self.last_rx = time.time()
        self.toast_until = 0.0
        self.eng = None                       # 판정 엔진 (= 와드 펌웨어 ward_infer.h). --model 일 때만
        self.dec = None                       # 마지막 판정
        if getattr(args, "model", None):
            import json
            from ward_engine import WardEngine
            model = json.loads(Path(args.model).read_text(encoding="utf-8"))
            self.air_label = model["air_label"]
            self.eng = WardEngine(model, model.get("on_labels", ["Coffee", "Vinegar"]))
            print(f"[판정] 와드와 같은 엔진, 모델 {self.eng.model_id}  향: {', '.join(model['classes']) or '없음'}"
                  f"  — 기준선은 와드처럼 1분 평평하면 자동 ([b] 로 지금 잡기)")
        self._build()

    # ---------- 그림 틀 ----------
    def _build(self):
        fig = plt.figure(figsize=(12, 8.4))
        show_hum = getattr(self.args, "humidity", False)
        ratios = [3.0, 1.1, 2.4] if show_hum else [3.0, 2.4]
        gs = fig.add_gridspec(len(ratios), 1, height_ratios=ratios, hspace=0.58,
                              left=0.075, right=0.975, top=0.875, bottom=0.115)
        self.fig = fig
        self.axA = fig.add_subplot(gs[0])
        self.axB = fig.add_subplot(gs[1], sharex=self.axA) if show_hum else None
        self.axC = fig.add_subplot(gs[-1])
        if show_hum:
            self.axA.tick_params(labelbottom=False)
        else:
            self.axA.set_xlabel("시간 (분)")

        self.lineA, = self.axA.plot([], [], color="black", lw=1.6, marker="o", ms=3)
        self.base_line = self.axA.axhline(0, color="tab:blue", ls="--", lw=1.1, visible=False)
        self.base_txt = self.axA.text(0.995, 0.03, "", transform=self.axA.transAxes,
                                      ha="right", va="bottom", fontsize=9, color="tab:blue")
        self.axA.set_ylabel("평균 log10(gas)")
        self.axA.set_title("8센서 평균  (히터 사이클 1회 = 점 1개)", loc="left", fontsize=11)
        self.axA.grid(alpha=0.3)

        if show_hum:
            self.lineB, = self.axB.plot([], [], color=C_UP, lw=1.4, marker="o", ms=2.5)
            self.axB.set_ylabel("칩 습도 %")
            self.axB.set_xlabel("시간 (분)")
            self.axB.set_title("습도 — 위 그래프와 같이 움직이면 가스가 아니라 방 환경 변화",
                               loc="left", fontsize=10)
            self.axB.grid(alpha=0.3)

        self.bars = self.axC.bar(range(N_STEPS), np.zeros(N_STEPS), color=C_DOWN, width=0.62)
        self.axC.axhline(0, color="black", lw=0.8)
        self.axC.set_xlim(-0.6, N_STEPS - 0.4)
        self.axC.set_ylabel("변화량 log10(gas)")     # Δ 는 세로 글씨에서 맑은 고딕으로 깨져 보임
        self.axC.grid(alpha=0.3, axis="y")
        self.dtext = self.axC.text(0.01, 0.97, "", transform=self.axC.transAxes,
                                   va="top", fontsize=14, fontweight="bold")
        self.ptext = self.axC.text(0.99, 0.97, "", transform=self.axC.transAxes,
                                   va="top", ha="right", fontsize=20, fontweight="bold")
        self._ticks(None)

        fig.text(0.075, 0.985, f"실시간 보기  {self.path.name}", va="top", fontsize=9, color=C_MUTED)
        self.status = fig.text(0.075, 0.955, "", va="top", fontsize=12, fontweight="bold")
        self.stab = fig.text(0.975, 0.955, "", va="top", ha="right", fontsize=12, fontweight="bold")
        self.toast = fig.text(0.975, 0.985, "", va="top", ha="right", fontsize=10, color="tab:blue")
        fig.text(0.5, 0.012, "[b] 지금을 기준선(Air)으로   [c] 기준선 해제   [q] 종료",
                 ha="center", fontsize=10, color=C_MUTED)
        fig.canvas.mpl_connect("key_press_event", self._on_key)

    def _ticks(self, temps):
        self.axC.set_xticks(range(N_STEPS))
        self.axC.set_xticklabels(
            [f"st{i}\n{temps[i]}°C" if temps else f"st{i}" for i in range(N_STEPS)], fontsize=9)

    def _say(self, msg):
        self.toast_until = time.time() + 3.5
        self.toast.set_text(msg)
        print("  " + msg)

    def _on_key(self, ev):
        if ev.key == "b":
            win = self.args.baseline_sec
            if self.st.set_baseline(win):
                if self.eng is not None:
                    self.eng.set_baseline(win)
                self._say(f"기준선 설정: 최근 {win:.0f}초 평균 (이 시점이 Air 여야 합니다)")
            else:
                self._say("기준선을 잡을 데이터가 아직 없습니다")
        elif ev.key == "c":
            self.st.clear_baseline()
            self._say("기준선 해제 (그래프만 — 판정 엔진은 그대로)")
        self.redraw()

    # ---------- 갱신 ----------
    def update(self):
        lines = self.tail.poll()
        if lines:
            self.last_rx = time.time()
            for ln in lines:
                self.st.feed(ln)
                if self.eng is not None:
                    self.dec = self.eng.feed(ln) or self.dec
        self.redraw()

    def _draw_pred(self):
        if self.eng is None:
            self.ptext.set_text("")
            return
        if not self.eng.has_base or self.dec is None:
            self.ptext.set_text("판정: 기준선 기다림 ([b] 로 지금)")
            self.ptext.set_color(C_MUTED)
            self.ptext.set_fontsize(13)
            return
        D = self.dec
        self.ptext.set_fontsize(20)
        col = C_UP if D.top == self.air_label else ("#8A6A14" if D.top == "Unknown" else C_DOWN)
        pct = self.eng.strength_pct(D.mag)
        name = {"Peppermint": "페퍼민트(추정)", "Lavender": "라벤더(추정)", "Orange": "오렌지(추정)",
                "Vinegar": "악취(식초)", "Unknown": "모르는 냄새", "Air": "깨끗함", "Weak": "약함"}.get(D.top, D.top)
        bar = "■" * round(pct / 10) + "□" * (10 - round(pct / 10))   # █░ 는 맑은 고딕에 없어 네모로 깨짐
        lines = [f"{name}  {D.k}/{D.n}", f"세기 {bar} {pct}%"]
        if D.purifier:
            lines.append("청정기 ON")
        self.ptext.set_text("\n".join(lines))
        self.ptext.set_fontsize(16)
        self.ptext.set_color(col)

    def redraw(self):
        st, W = self.st, self.args.window
        res = st.groups()
        now_min = st.sec_now / 60.0
        xmax = max(now_min, 0.5) + 0.05
        xmin = max(0.0, xmax - W) if W > 0 else 0.0
        self.axA.set_xlim(xmin, xmax)
        span = xmax - xmin
        major = 1.0 if span <= 12 else (5.0 if span <= 60 else 10.0)
        self.axA.xaxis.set_major_locator(MultipleLocator(major))
        self.axA.xaxis.set_minor_locator(MultipleLocator(major / 5.0))

        n_active, stab_txt, stab_col = 0, "", C_MUTED
        if res is not None:
            n_active, g = res
            t = g[:, 0] / 60.0
            vis = t >= xmin
            self.lineA.set_data(t, g[:, 1])
            extra = [st.base_avg] if st.base_avg is not None else []
            _set_ylim(self.axA, g[vis, 1], extra)
            if self.axB is not None:
                self.lineB.set_data(t, g[:, 2])
                _set_ylim(self.axB, g[vis, 2], min_span=0.5)
            m = g[:, 0] >= g[-1, 0] - 60
            if m.sum() >= 4:
                rng = float(np.ptp(g[m, 1]))
                if rng < 0.02:
                    stab_txt, stab_col = f"최근 1분 변동폭 {rng:.3f}  평평", C_UP
                elif rng < 0.05:
                    stab_txt, stab_col = f"최근 1분 변동폭 {rng:.3f}  약간 흔들림", "#8A6A14"
                else:
                    stab_txt, stab_col = f"최근 1분 변동폭 {rng:.3f}  변하는 중", C_DOWN

        if st.base_avg is not None:
            self.base_line.set_ydata([st.base_avg, st.base_avg])
            self.base_line.set_visible(True)
            self.base_txt.set_text(f"기준선 {st.base_avg:.3f}")
        else:
            self.base_line.set_visible(False)
            self.base_txt.set_text("")

        self._ticks(STEP_TEMPS)
        self._draw_delta()
        self._draw_pred()

        age = time.time() - self.last_rx
        if st.n_rows == 0:
            head, col = "● 데이터 대기 중", C_MUTED
        elif age < 3.5:
            head, col = "● 수신 중", C_UP
        else:
            head, col = f"■ 수신 멈춤 ({age:.0f}초 전까지)", C_DOWN
        reset = f"   [!] 보드 리셋 {st.resets}회" if st.resets else ""
        self.status.set_text(f"{head}   경과 {now_min:.1f}분   센서 {n_active or '-'}개{reset}")
        self.status.set_color(col)
        self.stab.set_text(stab_txt)
        self.stab.set_color(stab_col)
        if time.time() > self.toast_until:
            self.toast.set_text("")
        self.fig.canvas.draw_idle()

    def _draw_delta(self):
        d = self.st.delta()
        if d is None:
            for b in self.bars:
                b.set_height(0)
            self.axC.set_ylim(-1, 1)
            self.dtext.set_text("")
            self.axC.set_title("[b] 키를 눌러 지금 상태를 기준선(Air)으로 설정하면 변화량 Δ 가 보입니다",
                               loc="left", fontsize=10, color=C_MUTED)
            return
        d0 = np.nan_to_num(d)
        for b, v in zip(self.bars, d0):
            b.set_height(v)
            b.set_color(C_DOWN if v < 0 else C_UP)
        lim = max(0.5, 1.15 * float(np.abs(d0).max()))
        self.axC.set_ylim(-lim, lim)
        norm = float(np.linalg.norm(d0))
        txt = f"|Δ| = {norm:.2f}"
        ref = self.args.ref
        if ref:
            txt += f"      참고 {ref:g} 의 {100 * norm / ref:.0f}%"
        self.dtext.set_text(txt)
        self.axC.set_title(f"기준선 대비 스텝별 변화 (기준선: {self.st.base_sec / 60:.1f}분 시점, "
                           f"최근 {self.args.baseline_sec:.0f}초 평균)", loc="left", fontsize=10,
                           color="black")

    def run(self):
        def tick():
            try:
                self.update()
            except KeyboardInterrupt:
                plt.close(self.fig)
            except Exception as e:                # 한 번 틀려도 창은 계속 살려 둔다
                print(f"  (갱신 오류 무시: {e!r})")
        self.timer = self.fig.canvas.new_timer(interval=int(self.args.interval * 1000))
        self.timer.add_callback(tick)
        self.timer.start()
        tick()
        plt.show()


def newest_csv():
    from paths import TRAINING
    root = TRAINING / "recordings"
    files = list(root.rglob("*.csv")) if root.exists() else []
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="?", type=Path,
                    help="따라갈 CSV. 생략하면 training/recordings/ 아래 가장 최근 파일")
    ap.add_argument("--window", type=float, default=10.0,
                    help="보여줄 최근 구간(분). 0 이면 전체 (기본 10)")
    ap.add_argument("--interval", type=float, default=1.0, help="갱신 주기(초)")
    ap.add_argument("--humidity", action="store_true",
                    help="습도 그래프도 표시 (기본 숨김 — 판정에 안 씀. 분무 점검 때만)")
    ap.add_argument("--baseline-sec", type=float, default=60.0,
                    help="b 키를 눌렀을 때 평균 낼 최근 구간(초)")
    ap.add_argument("--ref", type=float, default=None,
                    help="|Δ| 비교 기준값(직접 지정, 농도 맞출 때). 생략하면 표시 안 함")
    ap.add_argument("--model", type=Path, default=None,
                    help="판정 모델(json, 예 ../model/openset_model.json). 주면 와드와 같은 판정을 표시")
    args = ap.parse_args()

    path = args.csv or newest_csv()
    if path is None or not Path(path).exists():
        sys.exit("따라갈 CSV 가 없습니다. capture_serial.py 로 녹화를 시작하거나 경로를 지정하세요.")
    print(f"[실시간] {path}")
    try:
        View(path, args).run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
