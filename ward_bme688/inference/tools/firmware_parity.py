#!/usr/bin/env python3
"""
firmware_parity.py
와드 펌웨어 판정 코드(firmware/ward_mqtt/ward_infer.h)의 **펌웨어 쪽 검사**.

11/09 부터 판정 규칙은 ward_infer.h 한 곳에만 있고, PC 도구(ward_controller·replay_eval·update_model)는
ward_engine.py 로 그 C 코드를 그대로 불러 쓴다 → 예전의 "파이썬과 C 판정 비교"는 필요 없어졌다.
남은 검사:
  1. 모델(json) → model.h (export_model.py 와 같은 변환) + ward_infer.h 를 gcc -Wall 로 컴파일 (펌웨어에 고정될 기본 모델)
  2. 무선 모델(글자 형식) 읽기 시험: 정상 모델은 받아들여 model.h 기본 모델과 판정이 같고, 망가진 모델 5종은 거절
  3. 녹화 재생: 펌웨어 시험 프로그램과 ward_engine 의 판정이 같은지 (빌드 설정 차이 확인)

사용:
  python firmware_parity.py                      # 모델 = 기본 모델 경로, 재생 = 가장 최근 녹화
  python firmware_parity.py ..\\training\\recordings\\rec_....csv
필요: gcc (MinGW).
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import MODEL, RECORDINGS, WARD_FW              # noqa: E402
from export_model import export, model_text               # noqa: E402
from ward_engine import WardEngine                        # noqa: E402

INFER_H = WARD_FW / "ward_infer.h"

# argv: csv rebase [model.txt]  — model.txt 를 주면 wi_model_parse 로 읽어 그 모델로 판정
HARNESS = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "ward_infer.h"
int main(int argc, char **argv) {
  FILE *f; char line[512]; wi_state_t *S = malloc(sizeof(wi_state_t));
  wi_params_t P = wi_default_params();
  if (argc > 2) P.rebase = (float)atof(argv[2]);
  wi_init(S, P);
  if (argc > 3) {
    static char txt[WI_MODEL_TEXT_MAX + 8]; char err[64]; size_t n; wi_model_t M;
    FILE *mf = fopen(argv[3], "rb"); if (!mf) return 3;
    n = fread(txt, 1, sizeof txt, mf); fclose(mf);
    if (!wi_model_parse(txt, n, &M, err, sizeof err)) { printf("PARSE_FAIL %s\n", err); return 2; }
    S->M = M;
    printf("PARSE_OK %s %d\n", S->M.id, S->M.n);
  }
  f = fopen(argv[1], "r");
  if (!f) return 1;
  while (fgets(line, sizeof line, f)) {
    double t; int s, k, valid; float T, Pp, H, g; unsigned mi, idac; char st[16];
    if (line[0] == '#' || line[0] == 'T') continue;
    if (sscanf(line, "%lf,%d,%f,%f,%f,%f,%d,%u,%u,%15[^,],%d", &t, &s, &T, &Pp, &H, &g, &k, &mi, &idac, st, &valid) != 11) continue;
    if (wi_feed(S, t, s, g, k, valid)) {
      wi_decision_t *D = &S->last;
      printf("%.3f,%s,%d,%d,%.4f,%d,%d,%d\n", D->sec, wi_label_name(S, D->top), D->k, D->n, D->mag, D->purifier, D->event, D->rebased);
    }
  }
  return 0;
}
"""


def _decisions(stdout):
    seq = []
    for ln in stdout.splitlines():
        if ln.startswith("PARSE_"):
            continue
        p = ln.split(",")
        seq.append((float(p[0]), p[1], p[5] == "1", int(p[2])))
    return seq


def build(model, on, td):
    shutil.copy(INFER_H, td / "ward_infer.h")
    export(model, set(on), td / "model.h")
    (td / "harness.c").write_text(HARNESS, encoding="utf-8")
    exe = td / "harness.exe"
    r = subprocess.run(["gcc", "-O2", "-std=c99", "-Wall", "-o", str(exe), str(td / "harness.c"), "-lm"],
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError("컴파일 실패:\n" + r.stderr)
    return exe, r.stderr.strip()


def parser_selftest(model, on, exe, csv, td, rebase):
    """무선 모델 글자 형식: 정상 → 받아들이고 기본 모델과 판정 동일 / 망가진 것 → 거절."""
    txt = model_text(model, set(on))
    good = td / "good.txt"
    good.write_text(txt, encoding="ascii", newline="\n")
    base = subprocess.run([str(exe), str(csv), str(rebase)], capture_output=True, text=True).stdout
    via = subprocess.run([str(exe), str(csv), str(rebase), str(good)], capture_output=True, text=True)
    ok_parse = via.stdout.startswith("PARSE_OK")
    same = _decisions(base) == _decisions(via.stdout)
    import zlib

    def recrc(t):                     # 내용을 고친 뒤 체크섬만 다시 맞춤 → 내용 검사가 잡는지 본다
        body = t.rsplit("crc ", 1)[0]
        return body + f"crc {zlib.crc32(body.encode('ascii')) & 0xFFFFFFFF:08x}\n"

    lines = txt.splitlines(keepends=True)
    ci = next(i for i, ln in enumerate(lines) if ln.startswith("class "))
    f = lines[ci].split()
    f_bad_mu = f[:4] + [f"{float(v) * 3:.7g}" for v in f[4:]]
    f_bad_ang = f[:3] + ["200"] + f[4:]
    bad_cases = {
        "체크섬 틀림": txt.replace("tau ", "tau 9", 1),
        "방향 길이 틀림": recrc("".join(lines[:ci] + [" ".join(f_bad_mu) + "\n"] + lines[ci + 1:])),
        "각도 범위 밖": recrc("".join(lines[:ci] + [" ".join(f_bad_ang) + "\n"] + lines[ci + 1:])),
        "crc 줄 없음": txt.rsplit("crc ", 1)[0],
        "빈 내용": "",
    }
    rejected = {}
    for name, t in bad_cases.items():
        pth = td / "bad.txt"
        pth.write_text(t, encoding="ascii", newline="\n")
        r = subprocess.run([str(exe), str(csv), str(rebase), str(pth)], capture_output=True, text=True)
        rejected[name] = (r.returncode == 2, r.stdout.strip().splitlines()[0] if r.stdout.strip() else "")
    return ok_parse and same and all(v[0] for v in rejected.values()), ok_parse, same, rejected


def engine_log(model, on, csv, rebase):
    e = WardEngine(model, on, rebase=rebase)
    seq = []
    for ln in csv.read_text(encoding="utf-8", errors="replace").splitlines():
        D = e.feed(ln.strip())
        if D:
            seq.append((round(D.sec, 3), D.top, D.purifier, D.k))
    return seq


def run(files=None, model_path=MODEL, rebase=180.0, verbose=True):
    """반환: dict(build_ok, parser_ok, engine_same, rounds)"""
    if shutil.which("gcc") is None:
        raise RuntimeError("gcc 가 없습니다 (MinGW 설치 필요).")
    model = json.loads(Path(model_path).read_text(encoding="utf-8"))
    on = model.get("on_labels", ["Coffee", "Vinegar"])
    files = files or sorted((p for p in RECORDINGS.glob("*.csv") if "discard" not in p.name),
                            key=lambda p: p.stat().st_mtime)[-1:]
    say = print if verbose else (lambda *a, **k: None)
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        exe, warn = build(model, on, td)
        say("[빌드] ward_infer.h + model.h 컴파일 ✓" + (f"\n[gcc 경고]\n{warn}" if warn else ""))
        say(f"[모델] {Path(model_path).name}  클래스 {', '.join(sorted(model['classes']))}  출동 {', '.join(on)}")
        same_all, rounds = True, 0
        for csv in files:
            fw = [(round(a, 3), b, c, d) for a, b, c, d in
                  _decisions(subprocess.run([str(exe), str(csv), str(rebase)], capture_output=True, text=True).stdout)]
            en = engine_log(model, on, csv, rebase)
            same = fw == en
            same_all &= same
            rounds += len(fw)
            say(f"[재생] {csv.name}  펌웨어 시험 프로그램 {len(fw)}판정 / 엔진 {len(en)}판정  {'같음 ✓' if same else '✗ 다름'}")
        all_ok, ok_parse, same, rejected = parser_selftest(model, on, exe, files[-1], td, rebase)
        say(f"[무선 모델 읽기 시험] 정상 모델 {'받아들임' if ok_parse else '✗ 거절'} · "
            f"기본 모델과 판정 {'같음' if same else '✗ 다름'}")
        for name, (rej, msg) in rejected.items():
            say(f"    {name:12s} → {'거절 ✓' if rej else '✗ 받아들임!'}  {msg}")
        return dict(build_ok=True, parser_ok=all_ok, engine_same=same_all, rounds=rounds)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="*", type=Path)
    ap.add_argument("--model", type=Path, default=MODEL)
    ap.add_argument("--rebase", type=float, default=180.0)
    args = ap.parse_args()
    try:
        run(args.csv or None, args.model, args.rebase)
    except RuntimeError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
