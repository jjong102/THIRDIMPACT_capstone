#!/usr/bin/env python3
"""
ward_engine.py
와드 판정 엔진 — 펌웨어의 ward_infer.h(C)를 PC 에서 그대로 불러 쓴다.

판정 규칙(기준선·Δ·각도 판정·표결·출동/끔·기준선 다시 잡기·갇힘 풀기)은 **ward_infer.h 한 곳에만** 있다.
ward_controller.py · replay_eval.py · update_model.py 는 모두 이 엔진을 통해 판정하므로
"노트북 재생 결과 = 와드 동작" 이 된다. 규칙을 바꾸려면 ward_infer.h 만 고친다.

  ward_infer.h + 작은 C 래퍼 ── gcc -shared ──▶ tools/.ward_engine/ward_engine_<해시>.dll(.so)
                                                    (헤더가 바뀌면 자동으로 다시 빌드)
  WardEngine.feed(측정 행)  → 판정이 나오면 Decision(sec, top, k, n, mag, purifier, event, rebased)

모델은 무선 교체와 같은 글자 형식(export_model.model_text)으로 넣는다 — 와드가 MQTT 로 받는 것과 같은 경로.
필요: gcc (Windows = MinGW-w64 64비트, Jetson = 기본 gcc).
"""

import ctypes
import hashlib
import shutil
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import WARD_FW                                 # noqa: E402
from export_model import model_text                       # noqa: E402

INFER_H = WARD_FW / "ward_infer.h"
BUILD_DIR = Path(__file__).resolve().parent / ".ward_engine"

WRAPPER = r"""
#include <stdlib.h>
#include <string.h>
#include "ward_infer.h"
#ifdef _WIN32
#define EX __declspec(dllexport)
#else
#define EX __attribute__((visibility("default")))
#endif
EX void *we_new(int on_cycles, int off_cycles, float min_on, float warmup, float flat, float track,
                float rebase, int rebase_weak, float spray_lock, float stuck_rebase) {
  wi_state_t *S = (wi_state_t *)calloc(1, sizeof(wi_state_t));
  wi_params_t P = {on_cycles, off_cycles, min_on, warmup, flat, track, rebase, rebase_weak, spray_lock, stuck_rebase};
  if (S) wi_init(S, P);
  return S;
}
EX void  we_free(void *S) { free(S); }
EX int   we_set_model(void *S_, const char *txt, int len, char *err, int errlen) {
  wi_state_t *S = (wi_state_t *)S_; wi_model_t M;
  if (!wi_model_parse(txt, (size_t)len, &M, err, (size_t)errlen)) return 0;
  S->M = M; S->pred_n = 0; S->pred_flag = 0;          /* 와드 model_apply() 와 같음 */
  return 1;
}
EX int   we_feed(void *S, double t_ms, int s, float gas, int k, int valid) {
  return wi_feed((wi_state_t *)S, t_ms, s, gas, k, valid);
}
EX void  we_last(void *S_, float *f, int *i) {
  wi_decision_t *D = &((wi_state_t *)S_)->last;
  f[0] = D->sec; f[1] = D->mag;
  i[0] = D->top; i[1] = D->k; i[2] = D->n; i[3] = D->purifier; i[4] = D->event; i[5] = D->rebased;
}
EX const char *we_label(void *S, int lab) { return wi_label_name((wi_state_t *)S, lab); }
EX const char *we_model_id(void *S) { return ((wi_state_t *)S)->M.id; }
EX float we_sec_now(void *S) { return ((wi_state_t *)S)->sec_now; }
EX int   we_has_base(void *S) { return ((wi_state_t *)S)->has_base; }
EX void  we_set_baseline(void *S, float win) { wi_set_baseline((wi_state_t *)S, win); }
EX void  we_note_spray(void *S, float sec, float dur) { wi_note_spray((wi_state_t *)S, sec, dur); }
EX void  we_set_lock(void *S, float until) { ((wi_state_t *)S)->lock_until = until; }
EX float we_lock(void *S) { return ((wi_state_t *)S)->lock_until; }
"""

Decision = namedtuple("Decision", "sec top k n mag purifier event rebased")
_lib = None


def _build():
    """ward_infer.h(+ model.h, 래퍼)가 바뀌었을 때만 다시 컴파일한다."""
    if not INFER_H.exists():
        raise RuntimeError(f"ward_infer.h 가 없습니다: {INFER_H}")
    h = hashlib.sha1()
    for p in (INFER_H, WARD_FW / "model.h"):
        h.update(p.read_bytes())
    h.update(WRAPPER.encode())
    ext = ".dll" if sys.platform == "win32" else ".so"
    lib = BUILD_DIR / f"ward_engine_{h.hexdigest()[:10]}{ext}"
    if lib.exists():
        return lib
    if shutil.which("gcc") is None:
        raise RuntimeError("gcc 가 없습니다 (Windows: MinGW-w64 64비트 설치, Jetson: sudo apt install gcc).")
    BUILD_DIR.mkdir(exist_ok=True)
    src = BUILD_DIR / "ward_engine.c"
    src.write_text(WRAPPER, encoding="utf-8")
    cmd = ["gcc", "-O2", "-std=c99", "-Wall", "-shared", "-I", str(WARD_FW), "-o", str(lib), str(src), "-lm"]
    if sys.platform != "win32":
        cmd.insert(1, "-fPIC")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError("판정 엔진 컴파일 실패:\n" + r.stderr)
    for old in BUILD_DIR.glob(f"ward_engine_*{ext}"):      # 이전 빌드 정리 (쓰는 중이면 남겨 둠)
        if old != lib:
            try:
                old.unlink()
            except OSError:
                pass
    return lib


def _load():
    global _lib
    if _lib is not None:
        return _lib
    L = ctypes.CDLL(str(_build()))
    vp, f, i, d, cp = ctypes.c_void_p, ctypes.c_float, ctypes.c_int, ctypes.c_double, ctypes.c_char_p
    L.we_new.restype = vp
    L.we_new.argtypes = [i, i, f, f, f, f, f, i, f, f]
    L.we_free.argtypes = [vp]
    L.we_set_model.restype = i
    L.we_set_model.argtypes = [vp, cp, i, cp, i]
    L.we_feed.restype = i
    L.we_feed.argtypes = [vp, d, i, f, i, i]
    L.we_last.argtypes = [vp, ctypes.POINTER(f), ctypes.POINTER(i)]
    for name in ("we_label",):
        getattr(L, name).restype = cp
        getattr(L, name).argtypes = [vp, i]
    L.we_model_id.restype = cp
    L.we_model_id.argtypes = [vp]
    for name in ("we_sec_now", "we_lock"):
        getattr(L, name).restype = f
        getattr(L, name).argtypes = [vp]
    L.we_has_base.restype = i
    L.we_has_base.argtypes = [vp]
    L.we_set_baseline.argtypes = [vp, f]
    L.we_note_spray.argtypes = [vp, f, f]
    L.we_set_lock.argtypes = [vp, f]
    _lib = L
    return L


def parse_row(line):
    """녹화 CSV 한 줄 → (t_ms, 센서, 가스저항, 단계, valid). 와드 펌웨어·시험 프로그램과 같은 11칸 형식."""
    if not line or line[0] in "#T":
        return None
    p = line.split(",")
    if len(p) < 11:
        return None
    try:
        return float(p[0]), int(p[1]), float(p[5]), int(p[6]), int(p[10])
    except ValueError:
        return None


class WardEngine:
    """와드 판정 상태 1개. 파라미터 이름은 ward_controller.py 옵션과 같다."""

    def __init__(self, model, on_labels, on_cycles=3, off_cycles=12, min_on=180.0, warmup=60.0, flat=0.05,
                 track=0.02, rebase=180.0, rebase_weak=False, spray_lock=180.0, stuck_rebase=60.0):
        self.L = _load()
        self.S = self.L.we_new(on_cycles, off_cycles, min_on, warmup, flat, track, rebase,
                               int(bool(rebase_weak)), spray_lock, stuck_rebase)
        if not self.S:
            raise MemoryError("판정 상태를 만들지 못했습니다")
        self.set_model(model, on_labels)
        self._f = (ctypes.c_float * 2)()
        self._i = (ctypes.c_int * 6)()

    def __del__(self):
        if getattr(self, "S", None):
            self.L.we_free(self.S)
            self.S = None

    def set_model(self, model, on_labels):
        txt = model_text(model, set(on_labels)).encode("ascii")
        err = ctypes.create_string_buffer(64)
        if not self.L.we_set_model(self.S, txt, len(txt), err, len(err)):
            raise ValueError(f"모델을 와드 형식으로 못 읽음: {err.value.decode('utf-8', 'replace')}")

    def feed_row(self, t_ms, s, gas, k, valid):
        if not self.L.we_feed(self.S, t_ms, s, gas, k, valid):
            return None
        self.L.we_last(self.S, self._f, self._i)
        f, i = self._f, self._i
        return Decision(f[0], self.label(i[0]), i[1], i[2], f[1], bool(i[3]), i[4], i[5])

    def feed(self, line):
        """녹화 CSV 한 줄. 판정이 나오면 Decision, 아니면 None."""
        r = parse_row(line)
        return self.feed_row(*r) if r else None

    def label(self, lab):
        return self.L.we_label(self.S, lab).decode("utf-8", "replace")

    @property
    def model_id(self):
        return self.L.we_model_id(self.S).decode("ascii", "replace")

    @property
    def sec_now(self):
        return self.L.we_sec_now(self.S)

    @property
    def has_base(self):
        return bool(self.L.we_has_base(self.S))

    def set_baseline(self, win=60.0):
        self.L.we_set_baseline(self.S, win)

    def note_spray(self, sec, dur=0.0):
        self.L.we_note_spray(self.S, sec, dur)

    def set_lock(self, until):
        self.L.we_set_lock(self.S, until)

    @property
    def lock_until(self):
        return self.L.we_lock(self.S)


if __name__ == "__main__":                       # 빌드 확인: python ward_engine.py [녹화.csv]
    import json
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    from paths import MODEL
    print(f"[엔진] {_build()}")
    if len(sys.argv) > 1:
        m = json.loads(MODEL.read_text(encoding="utf-8"))
        e = WardEngine(m, m.get("on_labels", ["Coffee", "Vinegar"]))
        n = ons = 0
        for ln in Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace").splitlines():
            D = e.feed(ln.strip())
            if D:
                n += 1
                ons += D.event == 1
        print(f"[재생] 모델 {e.model_id}  판정 {n}번  출동 {ons}번")
