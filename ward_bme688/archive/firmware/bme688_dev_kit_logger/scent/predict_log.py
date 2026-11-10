#!/usr/bin/env python3
"""
predict_log.py
MQTT 없이, SD 카드에 녹화한 CSV 로그를 그대로 넣어 추론한다.
실시간 시스템(infer_jetson.py)과 똑같은 처리를 오프라인으로 재현하므로,
모델이 새 데이터에서 실제로 동작하는지 먼저 확인할 수 있다.

=== 처리 순서 (실시간과 동일) ===
  1. 기준선 로그(Air)에서 센서별 Air 지문을 만든다
  2. 각 사이클마다: log10(가스저항) - 기준선 → 학습 때와 같은 정규화
  3. 사이클별 확률 → 8센서 평균 → 최근 5회 다수결(투표)

=== 사용법 ===
  # 기준선 = Air 로그, 나머지를 추론
  python predict_log.py --baseline LOG_0030.csv LOG_0031.csv LOG_0032.csv

  # ★ 권장: Air 로그를 여러 개 주면 각 로그에 '번호가 가장 가까운' 기준선을 자동 선택
  #    실측상 인접 기준선을 쓰면 확신도가 크게 올라간다 (0.53 → 1.00)
  python predict_log.py --baseline LOG_0022.csv LOG_0024.csv LOG_0026.csv \
                        Citrus=LOG_0023.csv Floral=LOG_0025.csv Woody=LOG_0027.csv

  # 정답을 알고 있으면 파일명에 라벨을 붙여 정확도까지 확인
  python predict_log.py --baseline LOG_0030.csv Citrus=LOG_0031.csv Woody=LOG_0033.csv

  # 시간에 따른 판정 변화를 보고 싶으면
  python predict_log.py --baseline LOG_0030.csv LOG_0031.csv --timeline

옵션:
  --model   모델 파일 (기본 scent_model.pkl)
  --vote    투표 창 크기 (기본 5)
  --timeline  시간순 판정 변화를 표로 출력
"""

import argparse
import sys
from collections import Counter, deque
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

N_STEPS = 10
ALIASES = {
    "time": ["TimeStamp(ms)", "TimeStamp", "timestamp"],
    "sensor": ["Sensor Index", "sensor_index"],
    "gas": ["Gas Resistance(ohm)", "Gas Resistance", "gas_resistance"],
    "step": ["Gas Index", "gas_index"],
    "valid": ["Gas Valid", "gas_valid"],
}


def file_number(path):
    """LOG_0023.csv → 23. 없으면 -1"""
    import re
    m = re.search(r"(\d+)", Path(path).stem)
    return int(m.group(1)) if m else -1


def load_cycles(path):
    """CSV → [(센서번호, 시각초, 10차원 log저항), ...] 시간순"""
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    col = {k: next((a for a in al if a in df.columns), None)
           for k, al in ALIASES.items()}
    for need in ("time", "sensor", "gas", "step"):
        if col[need] is None:
            raise KeyError(f"{Path(path).name}: '{need}' 컬럼 없음")

    w = pd.DataFrame({
        "t": pd.to_numeric(df[col["time"]], errors="coerce"),
        "s": pd.to_numeric(df[col["sensor"]], errors="coerce"),
        "step": pd.to_numeric(df[col["step"]], errors="coerce"),
        "gas": pd.to_numeric(df[col["gas"]], errors="coerce"),
    })
    if col["valid"]:                      # 더미 슬롯 제거 (실시간과 동일)
        w = w[pd.to_numeric(df[col["valid"]], errors="coerce").fillna(1) != 0]
    w = w.dropna(subset=["t", "s", "step", "gas"])
    w = w[w["gas"] > 0]
    w["s"] = w["s"].astype(int)
    w["step"] = w["step"].astype(int)

    out = []
    for s, g in w.groupby("s", sort=False):
        g = g.reset_index(drop=True)
        cid = (g["step"] < g["step"].shift(fill_value=-1)).cumsum()
        for _, cyc in g.groupby(cid):
            if sorted(cyc["step"].tolist()) != list(range(N_STEPS)):
                continue
            cyc = cyc.sort_values("step")
            out.append((s, cyc["t"].mean(),
                        np.log10(cyc["gas"].to_numpy())))
    if not out:
        return []
    t0 = min(o[1] for o in out)
    out = [(s, (t - t0) / 1000.0, v) for s, t, v in out]
    out.sort(key=lambda o: o[1])
    return out


def make_baseline(path, n_cycles=20):
    """Air 로그 앞부분으로 센서별 기준선 생성 (실시간 부트스트랩과 동일)"""
    cyc = load_cycles(path)
    per = {}
    for s, _, v in cyc:
        per.setdefault(s, []).append(v)
    base = {s: np.mean(v[:n_cycles], axis=0) for s, v in per.items()}
    print(f"기준선: {Path(path).name} / 센서 {sorted(base)} "
          f"/ 센서당 {min(len(v) for v in per.values())}사이클 중 앞 {n_cycles}개 사용")
    return base


def featurize(log_gas, base_vec, normalize):
    """학습과 동일한 변환"""
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
    ap.add_argument("--baseline", default="",
                    help="Air 로그. 쉼표로 여러 개 (번호가 가까운 것을 자동 선택). "
                         "생략하면 아래 files 중 'Air=' 로 표시한 것을 쓴다")
    ap.add_argument("--model", default="scent_model.pkl")
    ap.add_argument("--vote", type=int, default=5)
    ap.add_argument("--boot", type=int, default=20, help="기준선에 쓸 사이클 수")
    ap.add_argument("--timeline", action="store_true")
    ap.add_argument("files", nargs="+", help="추론할 로그들 (라벨=파일 형식 가능)")
    args = ap.parse_args()

    bundle = joblib.load(args.model)
    model = bundle["model"]
    normalize = bundle.get("normalize", "none")
    classes = list(model.classes_)
    print(f"모델: {bundle.get('name','?')} / 정규화: {normalize} / 클래스 {classes}\n")

    # 기준선 목록: --baseline 옵션 + files 중 Air= 로 표시된 것
    blist = [x for x in args.baseline.split(",") if x.strip()]
    for a in args.files:
        if "=" in a and a.split("=", 1)[0].strip().lower() == "air":
            blist.append(a.split("=", 1)[1])
    blist = list(dict.fromkeys(blist))          # 중복 제거, 순서 유지
    if not blist:
        sys.exit("기준선이 없습니다. --baseline 을 주거나 files 에 Air=파일 을 포함하세요")

    bases = {}
    for bp in blist:
        bases[file_number(bp)] = (bp, make_baseline(bp, args.boot))
    print()

    n_ok = n_all = 0
    for a in args.files:
        truth, path = a.split("=", 1) if "=" in a else (None, a)

        # 번호가 가장 가까운 기준선을 고른다 (실시간의 '최근 Air' 를 흉내)
        num = file_number(path)
        bnum = min(bases, key=lambda k: (abs(k - num), k))
        bpath, base = bases[bnum]

        cycles = load_cycles(path)
        if not cycles:
            print(f"[SKIP] {Path(path).name}: 완성된 사이클 없음")
            continue

        # --- 사이클별 예측 ---
        rows = []
        for s, sec, v in cycles:
            if s not in base:
                continue
            p = model.predict_proba(featurize(v, base[s], normalize))[0]
            rows.append((sec, p))
        if not rows:
            print(f"[SKIP] {Path(path).name}: 기준선에 없는 센서뿐")
            continue

        # --- 8센서 묶음 집계 + 투표 (실시간과 동일) ---
        votes = deque(maxlen=args.vote)
        timeline = []
        group, n_sens = [], max(1, len(base))
        for sec, p in rows:
            group.append(p)
            if len(group) < n_sens:
                continue
            m = np.mean(group, axis=0)
            group = []
            top = classes[int(m.argmax())]
            votes.append(top)
            voted, cnt = Counter(votes).most_common(1)[0]
            stable = len(votes) == args.vote and cnt > args.vote // 2
            timeline.append((sec, top, voted, stable, float(m.max())))

        # --- 결과 요약 ---
        # 최종 판정은 전체 사이클의 평균 확률로 (한 로그 전체를 요약할 때 가장 안정적)
        mean_p = np.mean([p for _, p in rows], axis=0)
        label = classes[int(mean_p.argmax())]
        conf = float(mean_p.max())
        per_cycle = Counter(t[1] for t in timeline)

        mark = ""
        if truth:
            n_all += 1
            ok = label == truth
            n_ok += ok
            mark = "  OK" if ok else f"  << 오답 (정답 {truth})"

        print(f"[{Path(path).name}]  판정: {label:<8} 확신 {conf*100:.0f}%"
              f"   (기준선 {Path(bpath).name}){mark}")
        print("   확률: " + "  ".join(f"{c} {p:.2f}"
                                     for c, p in zip(classes, mean_p)))
        dist = "  ".join(f"{k} {v}" for k, v in per_cycle.most_common())
        print(f"   사이클별 분포: {dist}")

        if args.timeline:
            print(f"   {'초':>6} {'사이클판정':<10} {'투표확정':<10} 확률")
            for sec, top, voted, stable, mx in timeline:
                flag = "확정" if stable else "수렴중"
                print(f"   {sec:>6.0f} {top:<10} {voted:<10} {mx:.2f}  {flag}")
        print()

    if n_all:
        print(f"=== 정답률: {n_ok}/{n_all} ({n_ok/n_all*100:.0f}%) ===")


if __name__ == "__main__":
    main()
