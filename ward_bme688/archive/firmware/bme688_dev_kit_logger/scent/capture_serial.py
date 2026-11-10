#!/usr/bin/env python3
"""
capture_serial.py
bme688_stream_full.ino 의 시리얼 출력을 그대로 CSV 로 녹화한다.
(SD카드 없이 노트북 직결 수집. 녹화 후 label_tool.py 로 구간을 잘라 라벨링)

  '#' 로 시작하는 안내 줄은 버리고, 헤더 + 데이터 행만 파일로 저장한다.
  출력 형식은 SD 로거와 동일 → parse_devkit_csv.py 가 그대로 파싱한다.

사용:
  python capture_serial.py --port COM5
  python capture_serial.py --port COM5 --out recordings --name woody_day1
  (Ctrl+C 로 종료)

의존성: pip install pyserial
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import serial  # pyserial
except ImportError:
    sys.exit("pyserial 필요: pip install pyserial")

# bme688_stream_full.ino 의 출력 컬럼 (SD 로거와 동일) — parse_devkit_csv.py 호환
HEADER = ("TimeStamp(ms),Sensor Index,Temperature(deg C),Pressure(Pa),"
          "Humidity(%),Gas Resistance(ohm),Gas Index,Meas Index,IDAC,"
          "Status,Gas Valid,Heater Stable")
N_FIELDS = len(HEADER.split(","))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True, help="예: COM5 또는 /dev/ttyACM0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--out", type=Path, default=Path("recordings"))
    ap.add_argument("--name", default=None, help="파일명에 붙일 태그(예: woody_day1)")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = f"_{args.name}" if args.name else ""
    out_path = args.out / f"rec_{stamp}{tag}.csv"

    print(f"[포트] {args.port} @ {args.baud}")
    print(f"[저장] {out_path}")
    print("녹화 중... (Ctrl+C 로 종료)\n")

    try:
        ser = serial.Serial(args.port, args.baud, timeout=1)
    except serial.SerialException as e:
        sys.exit(f"시리얼 열기 실패: {e}\n시리얼 모니터가 열려있지 않은지 확인하세요.")

    rows, bad = 0, 0
    t0 = time.time()
    try:
        with open(out_path, "w", encoding="utf-8", newline="") as f:
            # 헤더는 우리가 직접 쓴다.
            # (펌웨어는 부팅 때 한 번만 헤더를 내보내므로, 돌아가는 중에 붙으면 못 받는다)
            f.write(HEADER + "\n")

            while True:
                raw = ser.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                if line.startswith("#"):
                    print(f"  {line}")              # 안내 줄은 화면에만
                    continue
                if line.startswith("TimeStamp"):
                    continue                        # 펌웨어 헤더 — 이미 우리가 씀

                # 깨진 줄 거르기: 필드 수가 맞고 첫 칸이 숫자여야 함
                # (녹화 시작 시점에 전송 중이던 줄이 잘려 들어오는 경우 방지)
                parts = line.split(",")
                if len(parts) != N_FIELDS or not parts[0].isdigit():
                    bad += 1
                    continue

                f.write(line + "\n")
                rows += 1
                if rows % 200 == 0:
                    dt = time.time() - t0
                    print(f"\r  행 {rows}개  ({dt:5.0f}s)", end="", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        ser.close()
        print(f"\n\n[완료] {rows}행 저장 → {out_path}")
        if bad:
            print(f"  (깨진 줄 {bad}개 자동 제외)")
        if rows == 0:
            print("주의: 데이터가 없습니다. 포트/펌웨어(bme688_stream_full)를 확인하세요.")


if __name__ == "__main__":
    main()
