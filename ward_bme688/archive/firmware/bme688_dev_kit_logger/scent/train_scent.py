#!/usr/bin/env python3
"""
train_scent.py  (v3)

v3 변경점 — 세션 드리프트 대응
- [A] 특징 정규화 옵션 추가: 'none' vs 'cycle'
      MOX 가스센서는 시간이 지나면 저항 절대값이 통째로 흘러간다(드리프트).
      그러면 모델이 향이 아니라 "그 세션의 저항 수준"을 외워버려서,
      처음 보는 세션에서 정확도가 0에 수렴한다.
      'cycle' = 각 사이클의 10개 log 저항에서 자기 평균을 뺀다.
                → 지문의 '높이'를 버리고 '모양'만 남긴다. 세션 무관.
                → 추론 시 기준선 정보가 필요 없다(사이클 하나로 변환 가능).
      두 방식을 모두 돌려 비교표에 함께 출력한다.
- [B] LDA n_components 를 고정하지 않는다(None).
      GroupKFold 는 fold 에 따라 학습셋의 클래스 수가 줄 수 있어
      (클래스당 세션이 2개뿐이면 한 클래스가 통째로 test 로 빠짐)
      n_components 를 미리 박으면 ValueError 로 죽는다.
- [C] fold 별 클래스 커버리지 진단.
      학습셋에 없는 클래스가 test 에 있으면 그 fold 는 구조적으로 0점이다.
      해당 클래스는 세션을 더 모아야 한다는 신호이므로 명시적으로 경고한다.
- 비교 단계의 SVM 은 probability=False (속도), 최종 채택 모델만 True 로 재학습.

산출물:
  scent_model.pkl        최종 모델 + 특징명 + 정규화 방식
  model_comparison.csv   12개 조합 비교표 (보고서용)
  confusion_matrix.png   최고 조합의 혼동 행렬

사용법:
  python3 train_scent.py features.csv
"""

import os
import sys
import tempfile

# --- Windows + 한글 사용자명 대응 ---
# joblib 이 임시폴더 경로를 ASCII 로 인코딩하려다 실패하는 문제 방지
if not os.environ.get("JOBLIB_TEMP_FOLDER"):
    _tmp = os.path.join(tempfile.gettempdir(), "joblib_ascii")
    try:
        _tmp.encode("ascii")
    except UnicodeEncodeError:
        _tmp = r"C:\joblib_tmp" if os.name == "nt" else "/tmp/joblib_ascii"
    os.makedirs(_tmp, exist_ok=True)
    os.environ["JOBLIB_TEMP_FOLDER"] = _tmp

import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (ConfusionMatrixDisplay, classification_report,
                             confusion_matrix, f1_score)
from sklearn.model_selection import (GroupKFold, StratifiedGroupKFold,
                                     cross_val_predict)
from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

NORM_MODES = ("none", "cycle", "unit", "cycle_unit")
GAS_COLS = [f"log_gas_{i}" for i in range(10)]   # main() 에서 실제 개수로 교체된다


def detect_gas_cols(df):
    """log_gas_* 컬럼 개수를 자동 감지 (10 = 단일 프로파일, 40 = 4프로파일)"""
    n = 0
    while f"log_gas_{n}" in df.columns:
        n += 1
    if n == 0:
        sys.exit("log_gas_* 컬럼이 없습니다")
    return [f"log_gas_{i}" for i in range(n)]
EXTRA_COLS = ["temp", "hum"]
FEATURES = GAS_COLS + EXTRA_COLS


def make_features(df: pd.DataFrame, mode: str) -> np.ndarray:
    """[A] 특징 행렬 생성.
    mode='none'  : log 저항 10개 + 온습도 (원본)
    mode='cycle' : 각 사이클의 10개 log 저항에서 자기 평균을 뺀 값
                   → 저항 절대 수준(세션 드리프트)을 제거하고 모양만 남김
    mode='unit'  : 벡터 길이를 1로 맞춤 (L2 정규화)
                   → 기준선 차감 후의 벡터는 '크기=농도, 방향=향의 정체' 이므로
                     크기를 없애면 농도가 달라도 같은 향으로 인식된다
    mode='cycle_unit' : 평균 제거 후 길이 정규화 (둘 다 적용)
    """
    gas = df[GAS_COLS].to_numpy(dtype=float)

    # 40차원(4프로파일)이면 정규화를 프로파일 10개 단위로 따로 적용한다.
    # 프로파일마다 저항 수준과 신호 크기가 다르므로 통째로 정규화하면
    # 신호가 큰 프로파일 하나가 나머지를 눌러버린다.
    blocks = [gas[:, i:i + 10] for i in range(0, gas.shape[1], 10)] \
        if gas.shape[1] % 10 == 0 else [gas]

    out = []
    for b in blocks:
        if mode in ("cycle", "cycle_unit"):
            b = b - b.mean(axis=1, keepdims=True)
        if mode in ("unit", "cycle_unit"):
            n = np.linalg.norm(b, axis=1, keepdims=True)
            n[n == 0] = 1.0
            b = b / n
        out.append(b)
    gas = np.hstack(out)
    if FEATURES == GAS_COLS:        # 온습도 제외 모드
        return gas
    extra = df[EXTRA_COLS].to_numpy(dtype=float)
    return np.hstack([gas, extra])


def build_candidates():
    """전처리 2종 x 분류기 3종. LDA 는 n_components 를 고정하지 않는다 [B]."""
    clfs = {
        "RF": lambda: RandomForestClassifier(
            n_estimators=300, class_weight="balanced", random_state=42, n_jobs=-1),
        "SVM": lambda: SVC(kernel="rbf", C=10, class_weight="balanced",
                           cache_size=500, random_state=42),
        "KNN": lambda: KNeighborsClassifier(n_neighbors=7, weights="distance"),
    }
    out = {}
    for name, mk in clfs.items():
        out[f"Scaler+{name}"] = lambda mk=mk: make_pipeline(StandardScaler(), mk())
        out[f"PCA+LDA+{name}"] = lambda mk=mk: make_pipeline(
            StandardScaler(),
            PCA(n_components=8, random_state=42),
            LinearDiscriminantAnalysis(),      # n_components 자동 [B]
            mk())
    return out


def check_fold_coverage(y, groups, cv):
    """[C] fold 별로 학습셋에 빠진 클래스가 있는지 검사."""
    problems = []
    for k, (tr, te) in enumerate(cv.split(np.zeros(len(y)), y, groups)):
        missing = sorted(set(y[te]) - set(y[tr]))
        if missing:
            problems.append((k, missing, len(te)))
    return problems


def main(csv_path: str, use_th: bool = True):
    global FEATURES, GAS_COLS
    _df0 = pd.read_csv(csv_path, nrows=1)
    GAS_COLS = detect_gas_cols(_df0)
    FEATURES = GAS_COLS + EXTRA_COLS
    if len(GAS_COLS) == 40:
        print("특징 차원: 40 (히터 프로파일 4종)\n")
    if not use_th:
        FEATURES = GAS_COLS[:]      # 온습도 제외 (세션 식별 지름길로 악용됨)
        print(f"※ 온습도 제외 모드 (gas {len(GAS_COLS)}개만 사용)\n")
    df = pd.read_csv(csv_path)
    y = df["label"].to_numpy()
    groups = df["source_file"].to_numpy()
    labels = sorted(set(y))
    n_groups = len(np.unique(groups))

    print(f"샘플 {len(df)}개 / 클래스 {labels} / 세션 {n_groups}개")
    print(df["label"].value_counts(), "\n")
    if n_groups < 2:
        sys.exit("세션이 1개뿐이라 교차검증 불가")

    # ---- [D] 검증 분할: StratifiedGroupKFold ----
    # 일반 GroupKFold 는 세션 크기만 보고 나누기 때문에, 한 클래스의 세션이
    # 전부 같은 fold 로 몰려 "학습셋에 그 클래스가 없는" 상황이 생긴다.
    # (그 클래스는 구조적으로 0점이 되어 성능을 측정할 수 없다)
    # StratifiedGroupKFold 는 세션 경계를 지키면서 클래스 비율도 맞춰준다.
    # 분할 수는 '가장 세션이 적은 클래스의 세션 수' 를 넘을 수 없다.
    min_sess = min(len(np.unique(groups[y == lab])) for lab in labels)
    n_splits = max(2, min(5, min_sess))
    if min_sess < 2:
        sys.exit("세션이 1개뿐인 클래스가 있어 세션 분리 검증 불가")
    cv = StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=42)
    print(f"검증: StratifiedGroupKFold(n_splits={n_splits}) "
          f"— 클래스당 최소 세션 {min_sess}개\n")

    # ---- [C] fold 커버리지 진단 ----
    problems = check_fold_coverage(y, groups, cv)
    if problems:
        print("=== 경고: 학습셋에 클래스가 빠지는 fold 가 있음 ===")
        for k, missing, n in problems:
            print(f"  fold {k}: 학습셋에 {missing} 없음 → 해당 {n}개 샘플은 구조적으로 오답")
        print("  원인: 그 클래스의 세션이 모두 같은 fold 로 몰림 (클래스당 세션 부족)")
        print("  대응: 해당 클래스 세션을 3개 이상으로 늘릴 것\n")

    candidates = build_candidates()
    rows, preds = [], {}

    for norm in NORM_MODES:
        X = make_features(df, norm)
        for name, mk in candidates.items():
            try:
                y_pred = cross_val_predict(mk(), X, y, cv=cv, groups=groups, n_jobs=1)
            except Exception as e:
                print(f"{norm:>5} | {name:<15} 실패: {type(e).__name__}")
                continue
            macro = f1_score(y, y_pred, average="macro")
            acc = float((y_pred == y).mean())
            key = f"{norm}|{name}"
            rows.append({"normalize": norm, "model": name,
                         "macro_f1": round(macro, 4), "accuracy": round(acc, 4)})
            preds[key] = y_pred
            print(f"{norm:>5} | {name:<15} MacroF1={macro:.3f}  Acc={acc:.3f}")

    if not rows:
        sys.exit("모든 조합이 실패했습니다.")

    result = pd.DataFrame(rows).sort_values("macro_f1", ascending=False)
    result.to_csv("model_comparison.csv", index=False)
    print("\n→ model_comparison.csv 저장")

    best = result.iloc[0]
    best_key = f"{best['normalize']}|{best['model']}"
    print(f"\n=== BEST: {best['model']}  (normalize={best['normalize']}) ===")
    print(classification_report(y, preds[best_key], digits=3, zero_division=0))

    # 정규화 효과 요약
    by_norm = result.groupby("normalize")["macro_f1"].max()
    print("\n정규화별 최고 Macro F1:")
    for k in NORM_MODES:
        if k in by_norm:
            print(f"  {k:<12}{by_norm[k]:.3f}")
    if "unit" in by_norm and "none" in by_norm and by_norm["unit"] > by_norm["none"] + 0.05:
        print("  → 길이 정규화가 효과적 = 농도 차이가 주된 교란 요인이었음")

    cm = confusion_matrix(y, preds[best_key], labels=labels)
    disp = ConfusionMatrixDisplay(cm, display_labels=labels)
    fig, ax = plt.subplots(figsize=(7, 6))
    disp.plot(ax=ax, cmap="Blues", colorbar=False)
    ax.set_title(f"{best['model']} / norm={best['normalize']} (session-grouped CV)")
    fig.tight_layout()
    fig.savefig("confusion_matrix.png", dpi=150)
    print("→ confusion_matrix.png 저장")

    # ---- 최종 모델: 확률 출력 활성화하여 전체 데이터로 재학습 ----
    X_best = make_features(df, best["normalize"])
    pipe = candidates[best["model"]]()
    for step in pipe.named_steps.values():
        if isinstance(step, SVC):
            step.set_params(probability=True)
    pipe.fit(X_best, y)

    joblib.dump({"model": pipe, "features": FEATURES,
                 "normalize": best["normalize"], "name": best["model"]},
                "scent_model.pkl")
    print(f"→ scent_model.pkl 저장 ({best['model']}, normalize={best['normalize']})")
    print("\n※ infer_jetson.py 는 pkl 의 normalize 값을 읽어 같은 변환을 적용합니다.")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    main(args[0] if args else "features.csv",
         use_th="--no-temp-hum" not in flags)
