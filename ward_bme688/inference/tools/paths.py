"""
paths.py
작업 폴더의 기본 위치를 한 곳에 모아 둔다. 도구를 어느 폴더에서 실행해도 같은 곳을 쓴다.

두 가지 배치를 자동으로 알아본다.

  개발 폴더 (C:\\2026capston)                   팀 저장소 (ward_bme688/inference)
  ├── training/recordings/  원시 녹화           ├── training/recordings/   (같음, 커밋 안 함)
  ├── training/data/<라벨>/ 라벨 구간           ├── training/data/<라벨>/  (같음, 커밋 안 함)
  ├── inference/openset_model.json  판정 모델   ├── model/openset_model.json
  ├── inference/firmware/ward_mqtt/ 와드 펌웨어 ├── firmware/ward_mqtt/
  └── tools/  이 파일                           └── tools/
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRAINING = ROOT / "training"
RECORDINGS = TRAINING / "recordings"
DATA = TRAINING / "data"

if (ROOT / "inference" / "firmware").is_dir():          # 개발 폴더
    MODEL = ROOT / "inference" / "openset_model.json"
    WARD_FW = ROOT / "inference" / "firmware" / "ward_mqtt"
else:                                                   # 팀 저장소
    MODEL = ROOT / "model" / "openset_model.json"
    WARD_FW = ROOT / "firmware" / "ward_mqtt"

# 라벨링 창의 숫자키 1..8 순서 (docs/학습_계획.md). 다른 라벨은 --labels 로.
LABELS = ["Air", "Vinegar", "WetSock", "Sock", "Water", "Peppermint", "Lavender", "Orange"]
