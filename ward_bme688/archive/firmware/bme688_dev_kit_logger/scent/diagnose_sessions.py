#!/usr/bin/env python3
"""
diagnose_sessions.py
"세션 분리 검증에서 정확도가 붕괴한다"의 원인을 특정한다.

던지는 질문 4개:

  Q1. 향 신호가 애초에 존재하는가?
      → 같은 세션 안에서 무작위로 나눠 검증(random split).
        여기서도 낮으면 센서가 향을 구분 못 하는 것 (수집 조건 문제).
        여기선 높은데 세션 분리에서 낮으면 → 세션 교란 문제 (아래 계속).

  Q2. temp/hum 이 지름길로 쓰이고 있는가?
      → 가스 10개만 쓴 경우와 온습도를 포함한 경우를 비교.
        가스만 썼을 때 점수가 오르면 온습도가 세션 식별자로 악용된 것.

  Q3. 정규화가 드리프트를 걷어내는가?
      → none / cycle(사이클 평균 제거) / sensor(센서별 세션 기준선 제거) 비교.

  Q4. 라운드 간 일반화가 되는가?
      → 1라운드로 학습해 2라운드를 맞히기 (실전과 가장 비슷한 조건).

그리고 PCA 산점도 2장을 그린다.
  session_pca.png : 왼쪽=클래스로 색칠, 오른쪽=세션으로 색칠
  - 세션 색이 깔끔히 뭉치고 클래스 색이 섞여 있으면 → 세션이 지배 (교란 확정)
  - 클래스 색이 뭉치면 → 향 신호가 지배 (정상)

사용법:
  python3 diagnose_sessions.py features.csv
"""

import os
import sys
import tempfile

if not os.environ.get("JOBLIB_TEMP_FOLDER"):
    _t = os.path.join(tempfile.gettempdir(), "joblib_ascii")
    try:
        _t.encode("ascii")
    except UnicodeEncodeError:
        _t = r"C:\joblib_tmp" if os.name == "nt" else "/tmp/joblib_ascii"
    os.makedirs(_t, exist_ok=True)
    os.environ["JOBLIB_TEMP_FOLDER"] = _t

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import f1_score
from sklearn.model_selection import GroupKFold, StratifiedKFold, cross_val_predict
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

GAS = [f"log_gas_{i}" for i in range(10)]
TH = ["temp", "hum"]


def build_X(df, norm, use_th):
    g = df[GAS].to_numpy(float)
    if norm == "cycle":
        g = g - g.mean(axis=1, keepdims=True)
    elif norm == "sensor":
        # 센서별·세션별 기준선 제거: 그 세션 그 센서의 평균 지문을 빼준다
        g = g.copy()
        key = df["source_file"].astype(str) + "_" + df["sensor_index"].astype(str)
        for k in key.unique():
            m = (key == k).to_numpy()
            g[m] = g[m] - g[m].mean(axis=0, keepdims=True)
    if use_th:
        return np.hstack([g, df[TH].to_numpy(float)])
    return g


def rf():
    return make_pipeline(StandardScaler(),
                         RandomForestClassifier(n_estimators=200,
                                                class_weight="balanced",
                                                random_state=42, n_jobs=-1))


def main(path):
    df = pd.read_csv(path)
    y = df["label"].to_numpy()
    groups = df["source_file"].to_numpy()
    sessions = sorted(pd.unique(groups))
    print(f"샘플 {len(df)} / 클래스 {sorted(set(y))} / 세션 {len(sessions)}개\n")

    # ---------- Q1 + Q2 + Q3 ----------
    print("=== 검증 방식 x 특징 조합 (Macro F1) ===")
    print(f"{'정규화':<8}{'특징':<10}{'세션내 무작위':>14}{'세션 분리':>12}")
    rows = []
    for norm in ("none", "cycle", "sensor"):
        for use_th, tag in ((True, "gas+온습도"), (False, "gas만")):
            X = build_X(df, norm, use_th)

            skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=0)
            p_rand = cross_val_predict(rf(), X, y, cv=skf, n_jobs=1)
            f_rand = f1_score(y, p_rand, average="macro")

            gkf = GroupKFold(n_splits=min(5, len(sessions)))
            p_grp = cross_val_predict(rf(), X, y, cv=gkf, groups=groups, n_jobs=1)
            f_grp = f1_score(y, p_grp, average="macro")

            print(f"{norm:<8}{tag:<10}{f_rand:>14.3f}{f_grp:>12.3f}")
            rows.append({"normalize": norm, "features": tag,
                         "random_f1": round(f_rand, 4), "grouped_f1": round(f_grp, 4)})

    res = pd.DataFrame(rows)
    res.to_csv("diagnosis.csv", index=False)
    print("\n→ diagnosis.csv 저장")

    best_rand = res["random_f1"].max()
    best_grp = res["grouped_f1"].max()
    print("\n--- 해석 ---")
    if best_rand < 0.6:
        print("세션 안에서도 구분이 안 됨 → 향 신호 자체가 약함.")
        print("  원인 후보: 향 농도 부족, 센서와 거리가 멂, 환기 부족으로 클래스 간 오염")
    elif best_grp < 0.4:
        print("세션 안에서는 잘 맞히는데 처음 보는 세션에서 붕괴")
        print("  → 모델이 향이 아니라 '세션(시간대) 특성'을 외운 상태 = 세션 교란 확정")
        print("  → 해결은 알고리즘이 아니라 수집 설계에 있음 (아래 PCA 그림 확인)")
    else:
        print("일반화가 어느 정도 되고 있음. 세션을 늘리면 개선 여지 큼")

    gain = res.groupby("features")["grouped_f1"].max()
    if len(gain) == 2 and gain.get("gas만", 0) > gain.get("gas+온습도", 0) + 0.05:
        print("\n온습도를 빼면 점수가 오름 → temp/hum 이 세션 식별 지름길로 악용되고 있음")
        print("  → 특징에서 온습도를 제외할 것")

    # ---------- Q4: 라운드 간 일반화 ----------
    if len(sessions) >= 4:
        first = {}
        for lab in sorted(set(y)):
            s = sorted(pd.unique(groups[y == lab]))
            if len(s) >= 2:
                first[lab] = s[0]
        if len(first) == len(set(y)):
            tr = np.isin(groups, list(first.values()))
            if tr.sum() and (~tr).sum():
                X = build_X(df, "cycle", False)
                m = rf().fit(X[tr], y[tr])
                f = f1_score(y[~tr], m.predict(X[~tr]), average="macro")
                print(f"\n=== 1라운드 학습 → 2라운드 예측 (gas만, cycle) : Macro F1 {f:.3f} ===")
                print("  실전과 가장 비슷한 조건. 0.3 미만이면 라운드 간 드리프트가 지배적")

    # ---------- PCA 그림 ----------
    X = build_X(df, "cycle", False)
    Z = PCA(n_components=2, random_state=0).fit_transform(
        StandardScaler().fit_transform(X))
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))
    for ax, key, title in ((axes[0], y, "colored by CLASS"),
                           (axes[1], groups, "colored by SESSION")):
        for v in sorted(pd.unique(key)):
            m = key == v
            ax.scatter(Z[m, 0], Z[m, 1], s=4, alpha=0.4, label=str(v))
        ax.set_title(title)
        ax.legend(markerscale=3, fontsize=7)
    fig.suptitle("PCA (normalize=cycle, gas only)")
    fig.tight_layout()
    fig.savefig("session_pca.png", dpi=140)
    print("\n→ session_pca.png 저장")
    print("  오른쪽(세션)이 깔끔히 뭉치고 왼쪽(클래스)이 섞여 있으면 세션 교란 확정")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "features.csv")
