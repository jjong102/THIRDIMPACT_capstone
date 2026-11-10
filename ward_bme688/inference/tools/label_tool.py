#!/usr/bin/env python3
"""
label_tool.py
녹화 CSV(capture_serial.py 또는 SD 로거 LOG_*.csv)를 그래프로 띄우고,
마우스로 시간 구간을 드래그해 라벨을 붙여 클래스별 CSV 로 잘라 저장한다.
(Bosch AI-Studio 의 "그래프 보고 구간 라벨링" 을 소규모로 재현)

화면:
  위  = 8센서 평균 (향 들어오는 순간 저항 하락이 한눈에 보임 → 자르기 쉬움)
  아래 = 8센서 개별 (센서별 차이 확인)
  두 그래프는 x축(시간, 분 단위) 공유. 위 그래프에서 드래그해 구간 선택.

조작:
  마우스 드래그(위 그래프)  = 구간 선택
  숫자키 1~N               = 선택 구간을 그 라벨로 저장 (아래 --labels 순서)
  u                        = 마지막 저장 취소
  q / 창 닫기              = 종료

저장:
  data/<Label>/<파일이름>__seg<k>.csv   (원본 컬럼 그대로 → parse_devkit_csv.py 가 파싱)
  세그먼트 1개 = GroupKFold 세션 1개.

사용:
  python label_tool.py recordings/rec_20260921_woody.csv --labels Air Citrus Woody Smoke
의존성: pip install pandas numpy matplotlib
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator
from matplotlib.widgets import SpanSelector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import DATA, LABELS                      # noqa: E402

# 한글 폰트 (없으면 기본값 유지 — 깨짐 경고만 안 나게)
for _f in ("Malgun Gothic", "AppleGothic", "NanumGothic"):
    if any(_f == f.name for f in matplotlib.font_manager.fontManager.ttflist):
        matplotlib.rcParams["font.family"] = _f
        break
matplotlib.rcParams["axes.unicode_minus"] = False

ALIASES = {
    "time": ["TimeStamp(ms)", "TimeStamp", "timestamp"],
    "sensor": ["Sensor Index", "sensor_index", "sensor"],
    "gas": ["Gas Resistance(ohm)", "Gas Resistance", "gas_resistance", "gas"],
    "step": ["Gas Index", "gas_index", "step"],
    "gas_valid": ["Gas Valid", "gas_valid"],
}


def resolve(df, key):
    cols = {c.strip(): c for c in df.columns}
    for a in ALIASES[key]:
        if a in cols:
            return cols[a]
    return None


class Labeler:
    def __init__(self, csv_path: Path, labels: list[str], outdir: Path):
        self.labels = labels
        self.outdir = outdir
        self.stem = csv_path.stem
        # 같은 녹화를 다시 열어 구간을 추가해도 기존 파일을 덮어쓰지 않게 이어서 번호를 매긴다
        nums = [int(p.stem.rsplit("__seg", 1)[1]) for p in Path(outdir).glob(f"*/{self.stem}__seg*.csv")
                if p.stem.rsplit("__seg", 1)[1].isdigit()]
        self.seg_count = max(nums, default=0)
        self.saved = []          # (label, path, xmin, xmax, patch_handles)
        self.sel = None          # (xmin, xmax)

        df = pd.read_csv(csv_path)
        df.columns = [c.strip() for c in df.columns]
        tcol, scol = resolve(df, "time"), resolve(df, "sensor")
        gcol, stcol = resolve(df, "gas"), resolve(df, "step")
        vcol = resolve(df, "gas_valid")
        if not all([tcol, scol, gcol, stcol]):
            sys.exit("필수 컬럼 없음 (TimeStamp / Sensor Index / Gas Resistance / Gas Index)\n"
                     f"파일 컬럼: {list(df.columns)}")

        df["_t"] = pd.to_numeric(df[tcol], errors="coerce")
        df["_sensor"] = pd.to_numeric(df[scol], errors="coerce")
        df["_gas"] = pd.to_numeric(df[gcol], errors="coerce")
        df["_step"] = pd.to_numeric(df[stcol], errors="coerce")
        df = df.dropna(subset=["_t", "_sensor"])
        df["_sec"] = (df["_t"] - df["_t"].min()) / 1000.0
        self.df_all = df                    # 원본 전체 (내보내기용 — 더미행 포함)
        self.orig_cols = [c for c in df.columns if not c.startswith("_")]

        # ---- 그래프용: 더미(Gas Valid=0) 제거 후 "히터 사이클 1회 = 점 1개" ----
        v = df.dropna(subset=["_gas", "_step"]).copy()
        n_all = len(v)
        if vcol is not None:
            v = v[pd.to_numeric(v[vcol], errors="coerce").fillna(1) != 0]
        v = v[v["_gas"] > 0]
        if v.empty:
            sys.exit("유효한 가스 측정이 없습니다 (Gas Valid 가 전부 0)")
        v["_logg"] = np.log10(v["_gas"])
        print(f"  유효 측정 {len(v)}/{n_all}행 (더미 제외)")

        parts = []
        for s, g in v.groupby("_sensor", sort=True):
            g = g.sort_values("_t")
            step = g["_step"].to_numpy()
            cyc = np.concatenate([[0], np.cumsum(step[1:] < step[:-1])])
            agg = (g.assign(_cyc=cyc).groupby("_cyc")
                   .agg(sec=("_sec", "mean"), logg=("_logg", "mean"),
                        nstep=("_step", "nunique")).reset_index())
            # 10스텝이 다 모인 완전한 사이클만 사용.
            # (녹화를 Ctrl+C 로 끊으면 마지막 사이클이 잘려 값이 튄다)
            agg = agg[agg["nstep"] == 10]
            agg["sensor"] = int(s)
            parts.append(agg)
        self.cyc = pd.concat(parts, ignore_index=True)
        per = self.cyc.sort_values("sec").groupby("sensor")["sec"].diff().median()
        self.period = float(per) if pd.notna(per) and per > 0 else 10.0
        print(f"  사이클 {len(self.cyc)}개, 주기 약 {self.period:.1f}초")

        self._build_plot()

    def _build_plot(self):
        fig, (ax_avg, ax_each) = plt.subplots(
            2, 1, figsize=(13, 7), sharex=True,
            gridspec_kw={"height_ratios": [1, 1.4]})
        self.fig, self.ax_avg, self.ax_each = fig, ax_avg, ax_each

        # 위: 8센서 평균 (사이클 단위)
        grp = (self.cyc.assign(b=(self.cyc["sec"] // self.period).astype(int))
               .groupby("b").agg(sec=("sec", "mean"), logg=("logg", "mean"))
               .reset_index())
        ax_avg.plot(grp["sec"] / 60.0, grp["logg"], color="black", lw=1.6,
                    marker="o", ms=3)
        ax_avg.set_ylabel("평균 log10(gas)")
        ax_avg.set_title(f"위=8센서 평균  |  아래=센서별  "
                         f"(히터 사이클 1회 = 점 1개, 주기 {self.period:.1f}초)\n"
                         "위 그래프에서 드래그로 구간 선택 → 숫자키로 라벨")
        ax_avg.grid(alpha=0.3)

        # 아래: 센서별 (사이클 단위)
        cmap = plt.get_cmap("tab10")
        for s, g in self.cyc.groupby("sensor"):
            g = g.sort_values("sec")
            ax_each.plot(g["sec"] / 60.0, g["logg"], lw=1.0, marker="o", ms=2,
                         color=cmap(int(s) % 10), label=f"S{int(s)}")
        ax_each.set_ylabel("log10(gas)")
        ax_each.set_xlabel("시간 (분)")
        ax_each.grid(alpha=0.3)

        # 눈금 간격: 20분 넘으면 5분, 아니면 1분 (긴 녹화에서 라벨이 겹치지 않게)
        total_min = self.cyc["sec"].max() / 60.0
        major = 5.0 if total_min > 20 else 1.0
        for ax in (ax_avg, ax_each):
            ax.xaxis.set_major_locator(MultipleLocator(major))
            ax.xaxis.set_minor_locator(MultipleLocator(major / 5.0))
            ax.grid(which="minor", alpha=0.12)
        ax_each.legend(ncol=8, fontsize=8, loc="upper right")

        self.span = SpanSelector(
            ax_avg, self._on_select, "horizontal", useblit=True,
            props=dict(alpha=0.25, facecolor="tab:blue"), interactive=True)
        fig.canvas.mpl_connect("key_press_event", self._on_key)

        keymap = "  ".join(f"[{i+1}]{lab}" for i, lab in enumerate(self.labels))
        print("라벨 키:", keymap, "   [u]취소  [q]종료")
        fig.tight_layout()

    def _on_select(self, xmin, xmax):
        """xmin/xmax 는 그래프 좌표 = 분. 데이터는 초라서 저장할 때 환산한다."""
        if (xmax - xmin) * 60.0 < 3.0:          # 3초 미만은 오터치로 본다
            self.sel = None
            return
        self.sel = (xmin, xmax)
        print(f"  선택: {xmin:5.2f} ~ {xmax:5.2f}분   "
              f"({xmin*60:.0f}~{xmax*60:.0f}초, 길이 {(xmax-xmin)*60:.0f}초)"
              f"  → 라벨 숫자키를 누르세요")

    def _on_key(self, event):
        if event.key == "u":
            self._undo()
            return
        if event.key in [str(i + 1) for i in range(len(self.labels))]:
            if not self.sel:
                print("  먼저 위 그래프에서 구간을 드래그하세요")
                return
            self._save(self.labels[int(event.key) - 1])

    def _save(self, label):
        xmin, xmax = self.sel                       # 분 (그래프 좌표)
        s0, s1 = xmin * 60.0, xmax * 60.0           # 초 (데이터 좌표)
        seg = self.df_all[(self.df_all["_sec"] >= s0)
                          & (self.df_all["_sec"] <= s1)]
        if seg.empty:
            print("  구간에 데이터 없음")
            return
        self.seg_count += 1
        d = self.outdir / label
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{self.stem}__seg{self.seg_count}.csv"
        seg[self.orig_cols].to_csv(path, index=False)

        color = {"Air": "tab:green"}.get(label, "tab:red")
        p1 = self.ax_avg.axvspan(xmin, xmax, alpha=0.18, color=color)
        p2 = self.ax_each.axvspan(xmin, xmax, alpha=0.18, color=color)
        txt = self.ax_avg.text((xmin + xmax) / 2, self.ax_avg.get_ylim()[1],
                               label, ha="center", va="top", fontsize=9,
                               color=color, fontweight="bold")
        self.saved.append((label, path, [p1, p2, txt]))
        self.fig.canvas.draw_idle()
        print(f"  ✓ 저장 [{label}] {xmin:.2f}~{xmax:.2f}분, "
              f"{len(seg)}행 → {path}")
        self.sel = None

    def _undo(self):
        if not self.saved:
            print("  취소할 게 없음")
            return
        label, path, handles = self.saved.pop()
        try:
            path.unlink()
        except OSError:
            pass
        for h in handles:
            h.remove()
        self.fig.canvas.draw_idle()
        print(f"  ↩ 취소 [{label}] {path.name}")

    def run(self):
        plt.show()
        print(f"\n[완료] 저장한 세그먼트 {len(self.saved)}개")
        by = {}
        for label, _p, _h in self.saved:
            by[label] = by.get(label, 0) + 1
        for lab, n in sorted(by.items()):
            print(f"  {lab}: {n}개")
        if self.saved:
            print("\n다음 단계 예:")
            print("  python update_model.py      # 학습 → 검사 → 통과하면 교체")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", type=Path, help="녹화 CSV (capture_serial.py 또는 LOG_*.csv)")
    ap.add_argument("--labels", nargs="+",
                    default=LABELS,
                    help="라벨 목록 (숫자키 1..N 순서)")
    ap.add_argument("--outdir", type=Path, default=DATA,
                    help="세그먼트 저장 루트 (기본 training/data)")
    ap.add_argument("--png", nargs="?", const="", default=None,
                    help="창 없이 그래프만 PNG로 저장하고 종료 "
                         "(경로 생략 시 <CSV이름>.png)")
    args = ap.parse_args()
    if not args.csv.exists():
        sys.exit(f"파일 없음: {args.csv}")
    if args.png is not None:
        plt.switch_backend("Agg")
        lab = Labeler(args.csv, args.labels, args.outdir)
        out = Path(args.png) if args.png else args.csv.with_suffix(".png")
        lab.fig.savefig(out, dpi=150)
        print(f"[저장] {out}")
        return
    Labeler(args.csv, args.labels, args.outdir).run()


if __name__ == "__main__":
    main()
