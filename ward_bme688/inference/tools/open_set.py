#!/usr/bin/env python3
"""
open_set.py
아는 향은 이름으로, 모르는 냄새는 Unknown 으로 판정한다. (open-set 판정)

왜 따로 만들었나
  train_scent.py 의 분류기(RF·SVM·k-NN …)는 항상 "아는 클래스 중 하나"를 고른다.
  처음 보는 냄새도 가장 비슷한 향으로 우겨 넣는다. 실제로 과일 향 3종으로 학습한 뒤 망고를
  넣었더니 k-NN·SVM 은 100% 엉뚱한 클래스로 판정했고, 클래스마다 허용 범위(최소·최대·평균)를
  두는 방식은 100% 거부했다 (PMC5620598, "A False Alarm Reduction Method for a Gas Sensor
  Based Electronic Nose"). 이 도구는 그 허용 범위 방식을 우리 데이터에 맞게 옮긴 것이다.

판정 3단계 (센서 1개 · 히터 사이클 1회마다)
  Δ = 10스텝 log10(가스저항) − 인접 Air 기준선      (baseline_correct.py 와 같은 값)
  1) |Δ| < τ_air                     → Air       τ_air = Air 흔들림 |Δ| 99.5% 지점 × 1.3
  1') |Δ| < τ_decide = max(--decide-mult × τ_air, --decide-min) → Weak(보류) 청정기 상태 유지
       냄새는 있는데 모양을 믿기엔 약한 구간 (10/12 실측: |Δ| 0.2 안팎의 약한 라벤더 21% 가 커피 방향)
  2) 모양 u = Δ/|Δ| 가 향 c 의 허용 범위 안 → c
       z_c = max_i |u_i − μ_c,i| / σ_c,i   (스텝 하나라도 벗어나면 거리가 커진다)
       z_c ≤ z_thr_c 이면 허용. 여러 향이 허용하면 z_c/z_thr_c 가 가장 작은 쪽.
  3) 어디에도 안 맞으면              → Unknown
  그다음 8센서 다수결. 과반이 안 되면 Unknown.

  모양(단위벡터)만 보는 이유: 농도가 달라도 같은 향은 비슷한 모양이 나온다. 크기로 가르면
  농도가 바뀌는 순간 무너진다. "센서끼리 숫자는 안 맞아도 모양은 맞는다" (apiary-nose).

  τ_air 를 잴 흔들림 (--tau-mode)
    pooled (기본): Air 조각을 다른 Air 조각으로 차감한 값 = 기준선이 몇 분 낡았을 때의 흔들림.
                   조각 사이 차이(향이 남은 방, 습도 출렁임)까지 들어가 τ 가 크다(10/12 0.46).
    segment (실험): Air 조각 안의 흔들림만 = 기준선을 막 잡았을 때. τ 가 작아 약한 냄새를 잡는다.
  10/12 eval (1·2일차): pooled  → 꺼야 할 때 끔 100%, 켜야 할 때 켬 23%
                        segment → 끔 78%, 켬 41%   (보류 2배를 더하면 끔 94%, 켬 35%)
  → 커피(|Δ| 0.2~0.3)가 "몇 분 사이 공기 자체의 변화"(0.2~0.4)와 같은 크기라 문턱으로는 못 가른다.
    판정기를 고쳐서가 아니라 커피 신호를 키워야 풀린다 (docs/데모_학습계획.md).

  σ 에는 하한(--sigma-floor, 기본 0.02)을 둔다. 한 세션 안의 사이클은 거의 복사본이라 σ 가
  0.003 수준까지 작아져, 하한이 없으면 다른 날 같은 향도 전부 Unknown 이 된다.
  여러 날 데이터가 쌓이면 실제 σ 가 커지므로 하한의 영향이 줄어든다.

명령
  python open_set.py fit                            # training/data → inference/openset_model.json
  python open_set.py eval                           # 녹화 하나씩 빼고 검증 (진짜 성능)
  python open_set.py replay ..\\training\\recordings\\rec_....csv --base 0 120
                                                    # 녹화 파일을 시간순으로 판정
  실시간: python live_view.py --model ..\\inference\\openset_model.json   (b 로 기준선을 잡으면 판정 시작)

data 폴더 구조는 label_tool.py 가 만드는 그대로: <루트>/<라벨>/*.csv. Air 라벨 폴더가 꼭 있어야 한다.
★ 히터 설정이 다른 데이터를 섞지 말 것 (학습 펌웨어 ward_usb 와 와드 펌웨어 ward_mqtt 의 설정이 같아야 한다).
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse_devkit_csv import parse_file          # noqa: E402
from baseline_correct import apply_baseline      # noqa: E402
from session_id import session_info              # noqa: E402
from paths import DATA, MODEL                     # noqa: E402
from parse_devkit_csv import CYCLE_S              # noqa: E402

N_STEPS = 10
FEATS = [f"log_gas_{i}" for i in range(N_STEPS)]
AIR, UNKNOWN, WEAK = "Air", "Unknown", "Weak"


# ------------------------------------------------------------------ 데이터
def _quiet(*_a, **_k):
    pass


def load_dir(root: Path, ignore_heat_stab=True, air_label=AIR, verbose=False):
    """<root>/<라벨>/*.csv → 기준선 차감된 사이클 표 (행 = 센서 1개 · 사이클 1회)."""
    frames = []
    for sub in sorted(p for p in Path(root).iterdir() if p.is_dir()):
        for f in sorted(sub.rglob("*.csv")):
            try:
                feat, _ = parse_file(f, sub.name, True, ignore_heat_stab, None)
            except Exception as e:
                print(f"  [SKIP] {f.name}: {e}")
                continue
            if not feat.empty:
                frames.append(feat)
    if not frames:
        sys.exit(f"{root} 아래에서 읽은 사이클이 없습니다.")
    df = pd.concat(frames, ignore_index=True)
    try:
        out, dropped = apply_baseline(df, air_label, False, log=print if verbose else _quiet)
    except ValueError as e:
        sys.exit(str(e))
    if dropped:
        print(f"  (기준선 Air 를 못 찾아 제외된 사이클 {dropped}개)")
    out["recording"] = out["source_file"].map(lambda n: session_info(n)["recording"])
    return out


# ------------------------------------------------------------------ 모델
TEST_PREFIX = "Unk_"        # 이 접두사 라벨(예: Unk_식초)은 학습하지 않고 Unknown 시험에만 쓴다


def is_test_only(label, extra=()):
    return str(label).startswith(TEST_PREFIX) or label in set(extra)


def air_noise(df, air_label=AIR, mode="segment"):
    """Air 사이클의 흔들림 |Δ|.
    segment: Air 조각마다 (센서별) 자기 평균을 빼고 잰다 = 기준선을 막 잡았을 때의 흔들림.
    pooled : 다른 Air 조각으로 차감된 값 그대로 = 조각 사이 차이까지 포함 (v1)."""
    a = df[df["label"] == air_label]
    X = a[FEATS].to_numpy(float)
    if mode == "segment":
        X = X - a.groupby(["source_file", "sensor_index"])[FEATS].transform("mean").to_numpy(float)
    return np.linalg.norm(X, axis=1)


def _angle(u, mu):
    """단위벡터 사이 각도(도)."""
    return np.degrees(np.arccos(np.clip(np.asarray(u) @ np.asarray(mu), -1.0, 1.0)))


def fit(df, air_label=AIR, sigma_floor=0.02, air_margin=1.3, z_margin=1.25,
        z_min=3.0, min_cycles=15, test_only=(), tau_mode="pooled", decide_mult=1.0, decide_min=0.0,
        method="angle", angle_margin=1.5, angle_min=8.0):
    df = df[~df["label"].map(lambda l: is_test_only(l, test_only))]
    X = df[FEATS].to_numpy(float)
    mag = np.linalg.norm(X, axis=1)
    lab = df["label"].to_numpy()
    air = lab == air_label
    if air.sum() < 10:
        raise ValueError("Air 사이클이 너무 적습니다 (10개 미만).")
    tau = float(np.quantile(air_noise(df, air_label, tau_mode), 0.995) * air_margin)
    tau_decide = max(decide_mult * tau, decide_min)

    classes, skipped = {}, {}
    for c in sorted(set(lab) - {air_label}):
        m = (lab == c) & (mag > tau_decide)              # 향이 확실히 있는 사이클만
        if m.sum() < min_cycles:
            skipped[c] = int(m.sum())
            continue
        U = X[m] / mag[m, None]
        mu = U.mean(0)
        mu /= np.linalg.norm(mu)
        sig = np.maximum(U.std(0), sigma_floor)
        z = np.max(np.abs(U - mu) / sig, axis=1)
        ang = _angle(U, mu)
        classes[c] = {
            "mu": mu.round(5).tolist(), "sigma": sig.round(5).tolist(),
            "z_thr": float(max(np.quantile(z, 0.99) * z_margin, z_min)),
            "angle_thr": float(max(np.quantile(ang, 0.99) * angle_margin, angle_min)),
            "n_cycles": int(m.sum()),
            "recordings": sorted(df.loc[m, "recording"].unique().tolist()),
            "mag_p5": float(np.quantile(mag[m], 0.05)),
            "mag_p95": float(np.quantile(mag[m], 0.95)),
        }
    return {
        "kind": "open_set_v2",
        "method": method,
        "created": datetime.now().isoformat(timespec="seconds"),
        "air_label": air_label,
        "tau_air": tau,
        "tau_decide": tau_decide,
        "tau_mode": tau_mode,
        "air_cycles": int(air.sum()),
        "air_recordings": sorted(df.loc[air, "recording"].unique().tolist()),
        "sigma_floor": sigma_floor,
        "classes": classes,
        "skipped": skipped,
        "feature": "log10(가스저항) 10스텝 − 인접 Air 기준선 (센서별)",
    }


def predict_one(model, d):
    """Δ 벡터 1개 → (라벨, 상세). 상세: |Δ| 와 향별 z/z_thr."""
    d = np.asarray(d, float)
    if not np.all(np.isfinite(d)):
        return None, {}
    n = float(np.linalg.norm(d))
    info = {"mag": n}
    if n < model["tau_air"]:
        return model["air_label"], info
    if n < model.get("tau_decide", model["tau_air"]):
        return WEAK, info
    u = d / n
    if model.get("method", "z") == "angle":
        # 가장 가까운 방향의 향을 고르고, 그 향의 허용 각도 안이면 채택.
        # (z 방식은 퍼짐이 큰 향(커피, 여러 날)이 허용 폭도 넓어서, 우리 향에 가까운 사이클까지
        #  가져갔다 — 10/12 리플레이에서 남은 오렌지(오렌지와 2.5°)를 Coffee 로 판정)
        angs = {c: float(_angle(u, p["mu"])) for c, p in model["classes"].items()}
        info.update(angs)
        if not angs:
            return UNKNOWN, info
        c = min(angs, key=angs.get)
        return (c if angs[c] <= model["classes"][c]["angle_thr"] else UNKNOWN), info
    best, best_r = UNKNOWN, np.inf
    for c, p in model["classes"].items():
        z = float(np.max(np.abs(u - np.array(p["mu"])) / np.array(p["sigma"])))
        r = z / p["z_thr"]
        info[c] = r
        if r < best_r:
            best, best_r = c, r
    return (best if best_r <= 1.0 else UNKNOWN), info


def action_on(label, on_labels):
    """청정기를 켜야 하는 판정인가. Unknown 은 항상 켠다."""
    return label == UNKNOWN or label in set(on_labels)


def action_of(label, on_labels):
    """True = 켬, False = 끔, None = 보류(Weak·판정 없음 — 청정기 상태를 바꾸지 않음)."""
    if label in (None, WEAK):
        return None
    return action_on(label, on_labels)


def vote(labels, on_labels=None):
    """센서 다수결. 과반이 안 되면 Unknown.
    on_labels 를 주면, 이름으로는 과반이 안 돼도 '끔' 쪽 향(Air 제외)이 합쳐서 과반이면 그중 최다를 고른다.
    우리 향끼리는 모양이 2~4° 차이라 센서마다 Orange·Lavender 로 갈리는데, 그걸 Unknown(켬)으로
    만들면 안 되기 때문 (10/12 리플레이: 남은 오렌지가 'Orange 3 · Lavender 1 · Unknown 4' 로 갈려 청정기 켬)."""
    labels = [x for x in labels if x is not None]
    if not labels:
        return None, 0, 0
    top, k = Counter(labels).most_common(1)[0]
    if k * 2 > len(labels):
        return top, k, len(labels)
    if on_labels is not None:
        off = [l for l in labels if l not in (AIR, UNKNOWN, WEAK) and not action_on(l, on_labels)]
        if len(off) * 2 > len(labels):
            return Counter(off).most_common(1)[0][0], len(off), len(labels)
    return UNKNOWN, k, len(labels)


def describe(model):
    print(f"  σ 하한 {model['sigma_floor']}")
    print(f"  Air 문턱 τ = {model['tau_air']:.3f} ({model.get('tau_mode', 'pooled')})   "
          f"판정 문턱 {model.get('tau_decide', model['tau_air']):.3f}   "
          f"(Air {model['air_cycles']}사이클, 녹화 {len(model['air_recordings'])}개)")
    for c, p in model["classes"].items():
        print(f"  {c:10s} 사이클 {p['n_cycles']:4d}  녹화 {len(p['recordings'])}개  "
              f"|Δ| {p['mag_p5']:.2f}~{p['mag_p95']:.2f}  z_thr {p['z_thr']:.2f}")
        if len(p["recordings"]) < 2:
            print(f"  {'':10s} ⚠ 녹화가 1개뿐 — 다른 날 같은 향을 받아들일지는 아직 모릅니다")
    for c, n in model["skipped"].items():
        print(f"  {c:10s} 제외: 향이 확실한 사이클이 {n}개뿐 (15개 이상 필요)")


# ------------------------------------------------------------------ 검증
def evaluate(df, args):
    """녹화 하나씩 빼고 학습 → 뺀 녹화를 판정.
    뺀 녹화에만 있던 향은 학습에 없으므로 정답이 Unknown 이다 (처음 보는 냄새 시험)."""
    recs = sorted(df["recording"].unique())
    if len(recs) < 2:
        sys.exit("녹화가 2개 이상 있어야 검증할 수 있습니다.")
    rows = []
    print("\n=== 녹화 하나씩 빼고 검증 ===")
    for R in recs:
        tr, te = df[df["recording"] != R], df[df["recording"] == R]
        if (tr["label"] == args.air_label).sum() < 10:
            print(f"\n[{R}] 건너뜀 — 나머지에 Air 가 없음")
            continue
        model = fit(tr, args.air_label, args.sigma_floor, test_only=args.unknown,
                    tau_mode=args.tau_mode, decide_mult=args.decide_mult, decide_min=args.decide_min,
                    method=args.method, angle_min=args.angle_min)
        known = set(model["classes"])
        print(f"\n[{R}]  학습한 향: {', '.join(sorted(known)) or '없음'}")
        for lab, g in te.groupby("label"):
            exp = lab if (lab == args.air_label or lab in known) else UNKNOWN
            if is_test_only(lab, args.unknown):
                exp = UNKNOWN
            # 센서별 판정 → 같은 사이클(시각)끼리 다수결
            g = g.sort_values("t_ms")
            pred = [predict_one(model, r)[0] for r in g[FEATS].to_numpy(float)]
            g = g.assign(_p=pred, _bin=(g["t_ms"] // (CYCLE_S * 1000)))
            cyc = [vote(gg["_p"].tolist(), args.on)[0] for _, gg in g.groupby("_bin")]
            cnt = Counter(cyc)
            ok = cnt.get(exp, 0) / max(len(cyc), 1)
            # 청정기 기준: 보류(Weak)를 뺀 사이클 중 정답·판정이 둘 다 "켬"이거나 둘 다 "끔"이면 맞은 것.
            # 정답 동작은 실제 라벨로 정한다. 우리 향이 이번 학습에서 빠졌어도(녹화가 하나뿐) 실제로는 "끔"이
            # 맞다 — 데모에서는 학습돼 있을 향이므로. 진짜 처음 보는 냄새(Unk_ 라벨)만 "켬".
            want_on = action_on(UNKNOWN if is_test_only(lab, args.unknown) else lab, args.on)
            decided = {k: v for k, v in cnt.items() if action_of(k, args.on) is not None}
            nd = sum(decided.values())
            act = sum(v for k, v in decided.items() if action_on(k, args.on) == want_on) / max(nd, 1)
            hold = 1 - nd / max(len(cyc), 1)
            why = "" if exp == lab else "  (학습에 없던 냄새 → Unknown 이 정답)"
            dist = "  ".join(f"{k} {v}" for k, v in cnt.most_common())
            print(f"   {lab:10s} 정답 {exp:8s} 맞음 {ok*100:5.1f}%  청정기 {'켬' if want_on else '끔'} "
                  f"맞음 {act*100:5.1f}% (보류 {hold*100:4.1f}%)  [{dist}]{why}")
            rows.append(dict(rec=R, label=lab, expected=exp, n=len(cyc), ok=ok, act=act, want_on=want_on,
                             nd=nd, hold=hold,
                             **{f"p_{k}": v for k, v in cnt.items()}))
    res = pd.DataFrame(rows)

    print("\n=== 요약 (사이클 다수결 기준) ===")
    def line(name, sel, note=""):
        if not sel.any():
            print(f"  {name:28s} 시험할 데이터 없음{note}")
            return
        s = res[sel]
        print(f"  {name:28s} {np.average(s['ok'], weights=s['n'])*100:5.1f}%   "
              f"(녹화 {len(s)}개, 사이클 {int(s['n'].sum())}개){note}")
    line("Air → Air (오경보 없음)", res["label"] == args.air_label)
    line("처음 보는 향 → Unknown", res["expected"] == UNKNOWN)
    line("아는 향, 다른 녹화 → 그 향", (res["expected"] != UNKNOWN) & (res["label"] != args.air_label),
         "   ← 같은 향을 다른 날 2번 이상 찍어야 시험 가능")
    print(f"\n  청정기 판단 (켬 = {', '.join(args.on + [UNKNOWN])} / 끔 = 나머지 / 보류 = {WEAK})")
    for name, sel in [("켜야 할 때 켬", res["want_on"]), ("꺼야 할 때 끔", ~res["want_on"])]:
        if sel.any():
            s = res[sel]
            nd = s["nd"].sum()
            pct = (s["act"] * s["nd"]).sum() / nd * 100 if nd else float("nan")
            print(f"  {name:28s} {pct:5.1f}%   (판정 {int(nd)} / 사이클 {int(s['n'].sum())}, "
                  f"보류 {100 - nd / s['n'].sum() * 100:4.1f}%)")
    bad = res[res["expected"] == UNKNOWN]
    if not bad.empty:
        wrong = {k[2:]: int(bad[k].sum()) for k in bad.columns
                 if k.startswith("p_") and k[2:] not in (UNKNOWN, WEAK) and bad[k].sum() > 0}
        if wrong:
            print(f"  ⚠ 처음 보는 향을 아는 이름으로 잘못 부른 사이클: {wrong}")
    return res


# ------------------------------------------------------------------ 녹화 재생
def replay(path, model, base_win, step_s=30.0):
    feat, _ = parse_file(Path(path), "x", True, True, None)
    if feat.empty:
        sys.exit("사이클이 없습니다.")
    t0 = feat["t_ms"].min()
    feat["sec"] = (feat["t_ms"] - t0) / 1000.0
    b0, b1 = base_win
    base = feat[(feat["sec"] >= b0) & (feat["sec"] <= b1)].groupby("sensor_index")[FEATS].mean()
    if base.empty:
        sys.exit(f"기준선 구간 {b0}~{b1}초에 사이클이 없습니다.")
    per = 0
    print(f"기준선: {b0:.0f}~{b1:.0f}초 평균   ({Path(path).name})")
    print(f"{'구간':>12s}   판정        센서표   |Δ|")
    for lo in np.arange(0, feat["sec"].max() + 1, step_s):
        g = feat[(feat["sec"] >= lo) & (feat["sec"] < lo + step_s)]
        if g.empty:
            continue
        labs, mags = [], []
        for _, r in g.iterrows():
            s = r["sensor_index"]
            if s not in base.index:
                continue
            p, info = predict_one(model, r[FEATS].to_numpy(float) - base.loc[s].to_numpy(float))
            labs.append(p)
            mags.append(info.get("mag", np.nan))
        top, k, n = vote(labs, model.get("on_labels", ["Coffee"]))
        mark = "" if top in (AIR, None) else ("  ◀" if top != UNKNOWN else "  ?")
        print(f"  {lo/60:4.1f}~{(lo+step_s)/60:4.1f}분   {str(top):10s} {k:3d}/{n:<3d}  "
              f"{np.nanmedian(mags):5.2f}{mark}")
        per += 1


# ------------------------------------------------------------------ CLI
def main():
    ap = argparse.ArgumentParser(description="아는 향 / Air / Unknown 판정")
    sp = ap.add_subparsers(dest="cmd", required=True)
    for name in ("fit", "eval"):
        p = sp.add_parser(name)
        p.add_argument("data_dir", type=Path, nargs="?", default=DATA, help="라벨 폴더들의 루트 (기본 training/data)")
        p.add_argument("--air-label", default=AIR)
        p.add_argument("--sigma-floor", type=float, default=0.02,
                       help="모양 허용 범위의 최소 폭. 클수록 관대(다른 날 같은 향을 받아줌), "
                            "작을수록 엄격(처음 보는 냄새를 잘 거부)")
        p.add_argument("--no-ignore-heat-stab", action="store_true",
                       help="히터 불안정 사이클도 버림 (기본은 채택. 항상 --ignore-heat-stab 규칙)")
        p.add_argument("-v", "--verbose", action="store_true", help="기준선 짝짓기 과정 출력")
        p.add_argument("--unknown", nargs="*", default=[],
                       help=f"학습하지 않고 Unknown 시험에만 쓸 라벨 (접두사 {TEST_PREFIX} 라벨은 자동)")
        p.add_argument("--method", choices=["angle", "z"], default="angle",
                       help="모양 판정: angle=가장 가까운 방향(기본) / z=스텝별 허용 범위(v1)")
        p.add_argument("--angle-min", type=float, default=8.0,
                       help="향마다 허용 각도의 최소값(도). 10/12 실측: 같은 향 다른 날 2~4°, 우리 향↔커피 7~24°")
        p.add_argument("--tau-mode", choices=["segment", "pooled"], default="pooled",
                       help="Air 문턱을 잴 흔들림: pooled=조각 사이 차이 포함(기본, 오경보 0) / segment=Air 구간 안(민감, 실험)")
        p.add_argument("--decide-min", type=float, default=0.0,
                       help="판정 문턱의 최소값(|Δ|). 10/12 실측: 우리 향 방향이 맞는 비율 |Δ| 0.15~0.2 84%%, "
                            "0.2~0.25 90%%, 0.25 이상 100%% → 권장 0.3")
        p.add_argument("--decide-mult", type=float, default=1.0,
                       help="판정 문턱 = 이 값 × Air 문턱. 그 사이는 Weak(보류). 1(기본)이면 보류 없음")
        p.add_argument("--on", nargs="*", default=["Coffee", "Vinegar"],
                       help="청정기를 켜야 하는 향 라벨 (Unknown 은 항상 켬). 기본 Coffee Vinegar")
        if name == "fit":
            p.add_argument("--out", type=Path, default=MODEL, help="기본 inference/openset_model.json")
    p = sp.add_parser("replay")
    p.add_argument("csv", type=Path)
    p.add_argument("--model", type=Path, default=MODEL)
    p.add_argument("--base", type=float, nargs=2, default=(0, 120), metavar=("시작초", "끝초"),
                   help="이 녹화에서 Air 로 쓸 구간 (기본 0~120초)")
    p.add_argument("--step", type=float, default=30.0, help="출력 간격(초)")
    args = ap.parse_args()

    if args.cmd == "replay":
        model = json.loads(args.model.read_text(encoding="utf-8"))
        replay(args.csv, model, args.base, args.step)
        return

    print(f"=== 읽는 중: {args.data_dir} ===")
    df = load_dir(args.data_dir, not args.no_ignore_heat_stab, args.air_label, args.verbose)
    for lab, g in df.groupby("label"):
        print(f"  {lab:10s} 사이클 {len(g):5d}  녹화 {g['recording'].nunique()}개")

    if args.cmd == "fit":
        model = fit(df, args.air_label, args.sigma_floor, test_only=args.unknown,
                    tau_mode=args.tau_mode, decide_mult=args.decide_mult, decide_min=args.decide_min,
                    method=args.method, angle_min=args.angle_min)
        model["on_labels"] = args.on
        if not model["classes"]:
            print("\n⚠ 학습된 향이 없습니다. Air 말고 향 라벨 폴더가 있는지 확인하세요.")
        args.out.write_text(json.dumps(model, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n=== 모델 → {args.out} ===")
        describe(model)
        print("\n다음: python open_set.py eval", args.data_dir,
              "  /  실시간: python live_view.py --model", args.out)
    else:
        evaluate(df, args)


if __name__ == "__main__":
    main()
