#!/usr/bin/env python3
"""
capture_serial.py
와드 펌웨어(firmware/ward, USB 연결)의 시리얼 출력을 그대로 CSV 로 녹화한다.
(SD카드 없이 노트북 직결 수집. 녹화 후 label_tool.py 로 구간을 잘라 라벨링)

  '#' 로 시작하는 안내 줄은 버리고, 헤더 + 데이터 행만 파일로 저장한다.
  출력 형식은 SD 로거와 동일 → parse_devkit_csv.py 가 그대로 파싱한다.

녹화가 시작되면 live_view.py(실시간 그래프)가 자동으로 뜬다. 창을 닫아도 녹화는 계속된다.
Ctrl+C 로 녹화를 끝내면 실시간 창은 닫히고 label_tool.py(라벨링 UI)가 자동으로 뜬다.

사용:
  python capture_serial.py --port COM5 --name day1               # → training/recordings, 라벨 → training/data
  python capture_serial.py --port COM5 --labels Air Vinegar Unk_양파  # 라벨 목록 바꾸기
  python capture_serial.py --port COM5 --no-label                 # 녹화 후 라벨링 UI 안 띄움
  python capture_serial.py --port COM5 --no-live                  # 실시간 그래프 안 띄움

의존성: pip install pyserial
"""

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:                         # 출력이 파일·파이프로 갈 때(cp949) '—' 같은 문자에 죽지 않게
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import RECORDINGS, DATA, LABELS          # noqa: E402

try:
    import serial  # pyserial
except ImportError:          # mqtt_capture.py 가 이 파일의 함수만 가져다 쓸 때는 없어도 된다
    serial = None

# 와드 펌웨어(firmware/ward) 출력 컬럼 — parse_devkit_csv.py 호환
HEADER = ("TimeStamp(ms),Sensor Index,Temperature(deg C),Pressure(Pa),"
          "Humidity(%),Gas Resistance(ohm),Gas Index,Meas Index,IDAC,"
          "Status,Gas Valid,Heater Stable")
N_FIELDS = len(HEADER.split(","))



RESET_KEYS = ("rst:", "Brownout", "brownout", "Guru Meditation", "panic", "Backtrace", "abort()")


def start_live_view(csv_path):
    """live_view.py 를 별도 프로세스로 띄운다. 실패해도 녹화는 계속한다."""
    tool = Path(__file__).with_name("live_view.py")
    if not tool.exists():
        print("  (live_view.py 가 없어 실시간 그래프 생략)")
        return None
    kw = {}
    if sys.platform == "win32":
        # 콘솔의 Ctrl+C 가 그래프 창에 전달되지 않게 분리. 종료는 stop_live_view 가 맡는다.
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    try:
        p = subprocess.Popen([sys.executable, str(tool), str(csv_path)], **kw)
        print("  [실시간] 그래프 창을 띄웠습니다 (닫아도 녹화는 계속됩니다. 안 띄우려면 --no-live)\n")
        return p
    except OSError as e:
        print(f"  (실시간 그래프 실행 실패, 녹화는 계속: {e})")
        return None


def stop_live_view(p):
    if p is None or p.poll() is not None:
        return
    try:
        p.terminate()
        p.wait(timeout=3)
    except Exception:
        pass


def reconnect(old, port, baud, tries=15, wait=2.0):
    """포트가 끊겼을 때 재연결 시도. 성공하면 새 Serial, 실패하면 None."""
    try:
        old.close()
    except Exception:
        pass
    for i in range(1, tries + 1):
        time.sleep(wait)
        try:
            s = serial.Serial(port, baud, timeout=1)
            return s
        except serial.SerialException:
            print(f"  재연결 시도 {i}/{tries}...", flush=True)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True, help="예: COM5 또는 /dev/ttyACM0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--out", type=Path, default=RECORDINGS, help="기본 training/recordings")
    ap.add_argument("--name", default=None, help="파일명에 붙일 태그(예: woody_day1)")
    ap.add_argument("--labels", nargs="+",
                    default=LABELS,
                    help="녹화 후 자동 실행할 라벨링 UI 의 라벨 목록")
    ap.add_argument("--no-label", action="store_true",
                    help="녹화만 하고 라벨링 UI 를 띄우지 않음")
    ap.add_argument("--outdir", type=Path, default=DATA,
                    help="라벨 세그먼트 저장 루트 (기본 training/data)")
    ap.add_argument("--no-live", action="store_true",
                    help="녹화 중 실시간 그래프(live_view.py)를 띄우지 않음")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = f"_{args.name}" if args.name else ""
    out_path = args.out / f"rec_{stamp}{tag}.csv"

    print(f"[포트] {args.port} @ {args.baud}")
    print(f"[저장] {out_path}")
    print("녹화 중... (Ctrl+C 로 종료)\n")

    if serial is None:
        sys.exit("pyserial 필요: pip install pyserial")
    try:
        ser = serial.Serial(args.port, args.baud, timeout=1)
    except serial.SerialException as e:
        sys.exit(f"시리얼 열기 실패: {e}\n시리얼 모니터가 열려있지 않은지 확인하세요.")

    rows, bad, drops = 0, 0, 0
    live = None                         # 실시간 그래프 프로세스
    t0 = time.time()
    try:
        # buffering=1 = 줄 단위로 디스크에 내려 쓴다 → 실시간 창이 바로 읽을 수 있다
        with open(out_path, "w", encoding="utf-8", newline="", buffering=1) as f:
            # 헤더는 우리가 직접 쓴다.
            # (펌웨어는 부팅 때 한 번만 헤더를 내보내므로, 돌아가는 중에 붙으면 못 받는다)
            f.write(HEADER + "\n")
            if not args.no_live:
                live = start_live_view(out_path)

            while True:
                try:
                    raw = ser.readline()
                except serial.SerialException as e:
                    drops += 1
                    print(f"\n[경고] 시리얼 끊김: {e}")
                    ser = reconnect(ser, args.port, args.baud)
                    if ser is None:
                        print("[중단] 재연결 실패 — 여기까지 데이터는 저장됩니다.")
                        break
                    print("[복구] 재연결 성공, 녹화 계속\n"
                          "  주의: 보드가 리셋된 경우 TimeStamp 가 0부터 다시 시작합니다.\n"
                          "        라벨링 때 시간축이 튀면 이 파일은 끊긴 지점 기준으로 나눠 쓰세요.")
                    continue
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
                    # 보드가 리셋되면 ESP32 가 원인을 한 줄 찍는다 → 화면에 남긴다
                    #   Brownout = 전원 부족 / rst:0x1 POWERON = 전원 끊김 / Guru·panic = 펌웨어 오류
                    if any(k in line for k in RESET_KEYS):
                        print(f"\n[보드 리셋 원인] {line}")
                    continue

                f.write(line + "\n")
                rows += 1
                if rows % 200 == 0:
                    dt = time.time() - t0
                    print(f"\r  행 {rows}개  ({dt:5.0f}s)", end="", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            if ser is not None:
                ser.close()
        except Exception:
            pass
        print(f"\n\n[완료] {rows}행 저장 → {out_path}")
        if bad:
            print(f"  (깨진 줄 {bad}개 자동 제외)")
        if drops:
            print(f"  (시리얼 끊김 {drops}회 — 해당 구간 데이터 누락 가능)")
        if rows == 0:
            print("주의: 데이터가 없습니다. 포트/펌웨어(firmware/ward)를 확인하세요.")

    finish_recording(out_path, rows, live, args)


def finish_recording(out_path, rows, live, args):
    """녹화 종료 처리: 실시간 창 닫기 → 라벨링 UI. mqtt_capture.py 도 같이 쓴다.
    args 에는 no_label, labels, outdir 가 있어야 한다. 최종 파일 경로를 돌려준다."""
    stop_live_view(live)

    # ---- 녹화가 끝나면 라벨링 UI 자동 실행 ----
    if rows > 0 and not args.no_label:
        tool = Path(__file__).with_name("label_tool.py")
        if tool.exists():
            print(f"\n[라벨링] {tool.name} 실행 — 드래그로 구간 선택 후 숫자키로 라벨\n")
            cmd = [sys.executable, str(tool), str(out_path), "--labels", *args.labels]
            if args.outdir is not None:
                cmd += ["--outdir", str(args.outdir)]
            subprocess.run(cmd)
        else:
            print(f"\nlabel_tool.py 를 찾을 수 없습니다: {tool}")
    return out_path


if __name__ == "__main__":
    main()
