"""
paths.py
작업 폴더의 기본 위치를 한 곳에 모아 둔다. 도구를 어느 폴더에서 실행해도 같은 곳을 쓴다.
개발 폴더(C:\\2026capston)와 팀 저장소(ward_bme688/inference)는 같은 배치다 (tools/publish_team.py 가 그대로 올림).

  ├── firmware/ward/              와드 펌웨어 (수집·판정 하나, config.h 의 WARD_WIFI)
  ├── model/openset_model.json    판정 모델          model/history/  이전 모델
  ├── training/recordings/        원시 녹화          (커밋 안 함)
  ├── training/data/<라벨>/       라벨 구간          (커밋 안 함)
  └── tools/                      이 파일
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRAINING = ROOT / "training"
RECORDINGS = TRAINING / "recordings"
DATA = TRAINING / "data"
MODEL = ROOT / "model" / "openset_model.json"
MODEL_HISTORY = ROOT / "model" / "history"
WARD_FW = ROOT / "firmware" / "ward"

# 라벨링 창의 숫자키 1..8 순서 (docs/학습_계획.md). 다른 라벨은 --labels 로.
LABELS = ["Air", "Vinegar", "WetSock", "Sock", "Water", "Peppermint", "Lavender", "Orange"]
