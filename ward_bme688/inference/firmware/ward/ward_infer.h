/**
 * ward_infer.h — 와드 온디바이스 판정 (순수 C, Arduino 의존 없음)
 *
 * tools/ward_controller.py(+ live_view.Stream, open_set.predict_one/vote)를 그대로 옮긴 것.
 * PC 에서 gcc 로도 컴파일되어 tools/firmware_parity.py 가 파이썬과 판정이 같은지 확인한다.
 * 파이썬 쪽 규칙을 바꾸면 여기도 같이 바꾸고 parity 를 다시 돌릴 것.
 *
 *  측정 행 1개 ── wi_feed() ──▶ 센서별 사이클 조립 (Gas Index 0~9, 되감기면 사이클 끝)
 *                               │
 *        기준선 전: 라운드(센서 8개가 한 사이클씩) 평균이 최근 60초 평평하면 기준선 설정
 *        기준선 후: Δ = log10(R) − 기준선 → 세기 |Δ|, 방향 u → 판정(Air / 보류 / 클래스 / Unknown)
 *                               │  센서 8개가 다 모이면
 *                               ▼
 *        표결 → 켬/끔 쪽 연속 카운트 → 청정기 권고(ON/OFF) · 기준선 추종 · 평평하면 다시 잡기
 *
 * 모델: wi_model_t (클래스마다 평균 방향 10개 + 허용 각도, 문턱 2개).
 *   - 기본값은 model.h 의 WI_DEFAULT_MODEL (tools/export_model.py 가 만듦, 펌웨어에 고정)
 *   - 실행 중 교체: wi_model_parse() 로 글자 형식 모델을 읽어 S->M 에 넣는다
 *     (와드는 MQTT diffuser/<id>/model 로 받는다 — tools/push_model.py)
 *
 * 글자 형식 (줄 단위, 마지막 crc 줄은 그 앞 전부의 CRC-32 = zlib.crc32):
 *   WARDMODEL 1
 *   id 9845bdd19c
 *   tau 0.2280907 0.3
 *   class Lavender 0 44.0206 -0.06583 -0.06137 ... (10개)
 *   ...
 *   end
 *   crc 1a2b3c4d
 */
#pragma once
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define WI_NS          8     /* 센서 수 (DevKit) */
#define WI_STEPS       10    /* 히터 단계 수 */
#define WI_HIST        24    /* (센서, 단계)마다 기억할 최근 값 수 — 사이클당 1개, 약 4분 */
#define WI_ROUNDS      64    /* 평평한지 볼 라운드 기록 수 — 약 10분 */
#define WI_MAX_CLASSES 8
#define WI_NAME_LEN    16
#define WI_ID_LEN      16
#define WI_MODEL_TEXT_MAX 2048

typedef struct {
  char    name[WI_NAME_LEN];
  uint8_t on;                     /* 1 = 악취(출동) */
  float   angle_thr;              /* 허용 각도(도) */
  float   mu[WI_STEPS];           /* 평균 방향(단위벡터) */
} wi_class_t;

typedef struct {
  char       id[WI_ID_LEN];
  float      tau_air;             /* 이보다 작으면 Air */
  float      tau_decide;          /* 이보다 작으면 보류(Weak) */
  int        n;                   /* 클래스 수 */
  wi_class_t c[WI_MAX_CLASSES];
} wi_model_t;

#include "model.h"                /* static const wi_model_t WI_DEFAULT_MODEL */

/* 판정 라벨: 0..n-1 = 클래스, 음수 = 특수 */
enum { WI_AIR = -1, WI_WEAK = -2, WI_UNKNOWN = -3, WI_NONE = -4 };

typedef struct {                  /* ward_controller.py 기본값과 같음 */
  int   on_cycles;                /* 3   */
  int   off_cycles;               /* 12  */
  float min_on;                   /* 180 초 */
  float warmup;                   /* 60  초 */
  float flat;                     /* 0.05 */
  float track;                    /* 0.02 */
  float rebase;                   /* 180 초 (0 = 끔) */
  int   rebase_weak;              /* 0   */
  float spray_lock;               /* 180 초 */
  float stuck_rebase;             /* 60 초 (0 = 끔) — 켜진 채 켬 쪽이 평평하면 기준선 다시 */
} wi_params_t;

static inline wi_params_t wi_default_params(void) {
  wi_params_t p = {3, 12, 180.0f, 60.0f, 0.05f, 0.02f, 180.0f, 0, 180.0f, 60.0f};
  return p;
}

typedef struct {                  /* 한 번의 판정(라운드) 결과 */
  float sec;                      /* 녹화(부팅) 시작 기준 초 */
  int   top;                      /* 표결 결과 라벨 */
  int   k, n;                     /* 표 수 / 센서 수 */
  float mag;                      /* 센서별 |Δ| 중앙값 */
  int   purifier;                 /* 권고 상태 1 = ON */
  int   event;                    /* 0 없음, 1 = 방금 ON, 2 = 방금 OFF */
  int   rebased;                  /* 1 = 기준선 다시 잡음(평평한 공기), 2 = 켜진 채 갇힘을 풀려고 다시 잡음 */
} wi_decision_t;

typedef struct {
  wi_params_t P;
  wi_model_t  M;                  /* 지금 쓰는 모델 */
  /* 시간 */
  int    have_t0; double t0, last_t; float sec_now; int resets;
  /* 사이클 조립 */
  int    prev_k[WI_NS]; float steps[WI_NS][WI_STEPS]; uint16_t got[WI_NS];
  double tsum[WI_NS]; int tcnt[WI_NS];
  uint8_t seen;                   /* 측정이 한 번이라도 온 센서 */
  /* 최근 값 (기준선 계산용) */
  float  hx[WI_NS][WI_STEPS][WI_HIST], ht[WI_NS][WI_STEPS][WI_HIST];
  uint8_t hn[WI_NS][WI_STEPS], hi[WI_NS][WI_STEPS];
  /* 라운드 (센서 평균선) */
  float  cyc_t[WI_NS], cyc_m[WI_NS]; uint8_t cyc_flag;
  float  r_t[WI_ROUNDS], r_avg[WI_ROUNDS]; int r_n, r_i;
  /* 기준선 */
  int    has_base; float base[WI_NS][WI_STEPS]; uint16_t base_ok[WI_NS]; float base_sec;
  /* 이번 라운드 센서별 판정 (도착 순서) */
  int    pred_lab[WI_NS]; float pred_mag[WI_NS]; int8_t pred_ord[WI_NS]; int pred_n; uint8_t pred_flag;
  /* 제어 */
  int    purifier; float on_since; int run_on, run_off, air_run, calm_run, stuck_run; float lock_until;
  wi_decision_t last;
  int    n_decisions;
} wi_state_t;

/* ---------------------------------------------------------------- 도우미 */
static inline const char *wi_label_name(const wi_state_t *S, int lab) {
  if (lab >= 0 && lab < S->M.n) return S->M.c[lab].name;
  switch (lab) { case WI_AIR: return "Air"; case WI_WEAK: return "Weak";
                 case WI_UNKNOWN: return "Unknown"; default: return "-"; }
}

static inline int wi_count_bits(uint32_t x) { int c = 0; while (x) { c += x & 1u; x >>= 1; } return c; }

static inline float wi_median(const float *v, int n) {
  float a[WI_NS]; int i, j;
  if (n <= 0) return NAN;
  for (i = 0; i < n; i++) a[i] = v[i];
  for (i = 1; i < n; i++) { float x = a[i]; j = i - 1; while (j >= 0 && a[j] > x) { a[j + 1] = a[j]; j--; } a[j + 1] = x; }
  return (n & 1) ? a[n / 2] : 0.5f * (a[n / 2 - 1] + a[n / 2]);
}

/* 최근 win 초 라운드 평균선의 변동폭. 라운드가 5개 미만이면 -1 */
/* 최근 nr 라운드 평균선의 흔들림 (라운드가 모자라면 -1). 갇힘 풀기용 — 시간 경계 대신 개수로 세서 파이썬과 맞춘다 */
static inline float wi_round_ptp_n(const wi_state_t *S, int nr) {
  int i; float lo = 1e9f, hi = -1e9f;
  if (S->r_n < nr) return -1.0f;
  for (i = 0; i < nr; i++) {
    int idx = (S->r_i - 1 - i + WI_ROUNDS) % WI_ROUNDS;
    if (S->r_avg[idx] < lo) lo = S->r_avg[idx];
    if (S->r_avg[idx] > hi) hi = S->r_avg[idx];
  }
  return hi - lo;
}

static inline float wi_round_ptp(const wi_state_t *S, float since) {
  int i, cnt = 0; float lo = 1e9f, hi = -1e9f;
  for (i = 0; i < S->r_n; i++) {
    int idx = (S->r_i - 1 - i + WI_ROUNDS) % WI_ROUNDS;
    if (S->r_t[idx] < since) break;
    if (S->r_avg[idx] < lo) lo = S->r_avg[idx];
    if (S->r_avg[idx] > hi) hi = S->r_avg[idx];
    cnt++;
  }
  return cnt >= 5 ? hi - lo : -1.0f;
}

/* (센서, 단계)별 최근 win 초 평균 → out. 값이 없으면 0 반환 */
static inline int wi_recent_mean(const wi_state_t *S, int s, int k, float since, float *out) {
  int i, n = S->hn[s][k], c = 0; double sum = 0;
  for (i = 0; i < n; i++) {
    int idx = (S->hi[s][k] - 1 - i + WI_HIST) % WI_HIST;
    if (S->ht[s][k][idx] < since) break;
    sum += S->hx[s][k][idx]; c++;
  }
  if (!c) return 0;
  *out = (float)(sum / c);
  return 1;
}

static inline void wi_set_baseline(wi_state_t *S, float win) {
  int s, k; float m;
  for (s = 0; s < WI_NS; s++) {
    S->base_ok[s] = 0;
    for (k = 0; k < WI_STEPS; k++)
      if (wi_recent_mean(S, s, k, S->sec_now - win, &m)) { S->base[s][k] = m; S->base_ok[s] |= (uint16_t)(1u << k); }
  }
  S->has_base = 1; S->base_sec = S->sec_now;
}

/* ---------------------------------------------------------------- 판정 */
static inline int wi_predict(const wi_model_t *M, const float *d, float *mag_out) {
  int k, c, best = -1; float n = 0, bang = 1e9f;
  for (k = 0; k < WI_STEPS; k++) { if (!isfinite(d[k])) { *mag_out = NAN; return WI_NONE; } n += d[k] * d[k]; }
  n = sqrtf(n); *mag_out = n;
  if (n < M->tau_air) return WI_AIR;
  if (n < M->tau_decide) return WI_WEAK;
  for (c = 0; c < M->n; c++) {
    float dot = 0, a;
    for (k = 0; k < WI_STEPS; k++) dot += (d[k] / n) * M->c[c].mu[k];
    if (dot > 1) dot = 1;
    if (dot < -1) dot = -1;
    a = acosf(dot) * 57.29577951f;
    if (a < bang) { bang = a; best = c; }
  }
  if (best < 0) return WI_UNKNOWN;
  return bang <= M->c[best].angle_thr ? best : WI_UNKNOWN;
}

static inline int wi_is_on(const wi_model_t *M, int lab) { return lab == WI_UNKNOWN || (lab >= 0 && M->c[lab].on); }
static inline int wi_is_off_class(const wi_model_t *M, int lab) { return lab >= 0 && !M->c[lab].on; }

/* open_set.vote: 과반이면 그 라벨, 아니면 '끔' 쪽 향(Air 제외)이 합쳐 과반이면 그중 최다, 아니면 Unknown.
   동률은 먼저 나온 라벨 (파이썬 Counter.most_common 과 같음) */
static inline int wi_vote(const wi_model_t *M, const int *labs, int n, int *k_out) {
  int i, j, top = WI_NONE, topc = 0, off = 0, offtop = WI_NONE, offc = 0;
  if (!n) { *k_out = 0; return WI_NONE; }
  for (i = 0; i < n; i++) {
    int c = 0; for (j = 0; j < n; j++) if (labs[j] == labs[i]) c++;
    if (c > topc) { topc = c; top = labs[i]; }
  }
  if (topc * 2 > n) { *k_out = topc; return top; }
  for (i = 0; i < n; i++) if (wi_is_off_class(M, labs[i])) off++;
  if (off * 2 > n) {
    for (i = 0; i < n; i++) {
      int c = 0; if (!wi_is_off_class(M, labs[i])) continue;
      for (j = 0; j < n; j++) if (labs[j] == labs[i]) c++;
      if (c > offc) { offc = c; offtop = labs[i]; }
    }
    *k_out = off; return offtop;
  }
  /* 켬 쪽(Unknown·악취 클래스)도 과반이 아니면 보류 — 4/8 Unknown + 4/8 Weak 같은 경계 신호로 출동하지 않게 (11/09) */
  {
    int on = 0;
    for (i = 0; i < n; i++) if (wi_is_on(M, labs[i])) on++;
    if (on * 2 > n) { *k_out = on; return WI_UNKNOWN; }
  }
  *k_out = topc; return WI_WEAK;
}

static inline void wi_note_spray(wi_state_t *S, float sec, float dur) {
  if (S->P.spray_lock > 0 && sec + dur + S->P.spray_lock > S->lock_until) S->lock_until = sec + dur + S->P.spray_lock;
}

static inline void wi_decide(wi_state_t *S, float sec) {
  int labs[WI_NS], i, k, n = 0, top, want; float mags[WI_NS], med;
  wi_decision_t D;
  for (i = 0; i < S->pred_n; i++) {
    int s = S->pred_ord[i];
    if (S->pred_lab[s] == WI_NONE) continue;
    labs[n] = S->pred_lab[s]; mags[n] = S->pred_mag[s]; n++;
  }
  S->pred_n = 0; S->pred_flag = 0;
  med = wi_median(mags, n);
  top = wi_vote(&S->M, labs, n, &k);
  want = (top == WI_WEAK || top == WI_NONE) ? -1 : wi_is_on(&S->M, top);
  if (want == 1 && top == WI_UNKNOWN && sec < S->lock_until) want = -1;
  S->air_run = (top == WI_AIR) ? S->air_run + 1 : 0;
  if (want == 1) { S->run_on++; S->run_off = 0; }
  else if (want == 0) { S->run_on = 0; S->run_off++; }
  D.event = 0;
  if (!S->purifier && S->run_on >= S->P.on_cycles) { S->purifier = 1; S->on_since = sec; D.event = 1; }
  else if (S->purifier && S->run_off >= S->P.off_cycles && sec - S->on_since >= S->P.min_on) { S->purifier = 0; D.event = 2; }
  /* 기준선 추종: Air 가 6사이클 이상, 변화량이 문턱 절반 아래일 때만 아주 조금 */
  if (S->air_run >= 6 && med <= S->M.tau_air / 2) {
    int s, st; float m, a = S->P.track;          /* st: 단계 번호 (k 는 표 수라 덮어쓰면 안 됨 — 11/04 '10/8' 버그) */
    for (s = 0; s < WI_NS; s++) for (st = 0; st < WI_STEPS; st++)
      if ((S->base_ok[s] >> st) & 1u)
        if (wi_recent_mean(S, s, st, S->sec_now - 60.0f, &m)) S->base[s][st] = (1 - a) * S->base[s][st] + a * m;
  }
  /* 기준선 다시 잡기: 청정기 꺼진 채 Air(옵션: 보류)가 rebase 초 평평 */
  D.rebased = 0;
  if (S->P.rebase > 0) {
    int calm = (top == WI_AIR) || (S->P.rebase_weak && top == WI_WEAK);
    if (S->purifier || !calm || sec < S->lock_until) S->calm_run = 0;
    else {
      S->calm_run++;
      if (S->calm_run * 9.7f >= S->P.rebase) {
        float p = wi_round_ptp(S, sec - S->P.rebase);
        if (p >= 0 && p < S->P.flat) { wi_set_baseline(S, 60.0f); S->calm_run = 0; D.rebased = 1; }
      }
    }
  }
  /* 갇힘 풀기: 청정기가 켜진 채 켬 쪽 판정이 stuck_rebase 초 동안 평평하게 이어지면 기준선이 어긋난 것으로 보고
     다시 잡는다. 실제 악취는 치우면 줄어들지만 어긋난 기준선은 세기가 일정하게 멈춘다 (11/04: |Δ| 0.38 로 15분 켜짐). */
  if (S->P.stuck_rebase > 0 && S->purifier && want == 1) {
    S->stuck_run++;
    if (S->stuck_run * 9.7f >= S->P.stuck_rebase && sec - S->on_since >= S->P.min_on) {
      int nr = (int)(S->P.stuck_rebase / 9.7f + 0.5f); if (nr < 5) nr = 5;
      float p = wi_round_ptp_n(S, nr);
      if (p >= 0 && p < S->P.flat) { wi_set_baseline(S, 60.0f); S->stuck_run = 0; D.rebased = 2; }
    }
  } else S->stuck_run = 0;
  D.sec = sec; D.top = top; D.k = k; D.n = n; D.mag = med; D.purifier = S->purifier;
  S->last = D; S->n_decisions++;
}

/* ---------------------------------------------------------------- 입력 */
static inline void wi_init(wi_state_t *S, wi_params_t P) {
  int s; memset(S, 0, sizeof(*S)); S->P = P; S->lock_until = -1.0f;
  S->M = WI_DEFAULT_MODEL;
  for (s = 0; s < WI_NS; s++) S->prev_k[s] = -1;
}

/* 사이클 1개 완성 (센서 s, 시각 t = 그 사이클 측정 시각 평균, 10단계 log) */
static inline int wi_on_cycle(wi_state_t *S, int s, float t, const float *x) {
  int k, decided = 0; float m = 0;
  for (k = 0; k < WI_STEPS; k++) m += x[k];
  /* 라운드: 측정된 센서가 다 한 번씩 사이클을 끝내면 평균선 점 1개 */
  S->cyc_t[s] = t; S->cyc_m[s] = m / WI_STEPS; S->cyc_flag |= (uint8_t)(1u << s);
  if ((S->cyc_flag & S->seen) == S->seen) {
    int i, c = 0; float st = 0, sm = 0;
    for (i = 0; i < WI_NS; i++) if ((S->seen >> i) & 1u) { st += S->cyc_t[i]; sm += S->cyc_m[i]; c++; }
    S->r_t[S->r_i] = st / c; S->r_avg[S->r_i] = sm / c;
    S->r_i = (S->r_i + 1) % WI_ROUNDS; if (S->r_n < WI_ROUNDS) S->r_n++;
    S->cyc_flag = 0;
    if (!S->has_base) {
      float now = S->r_t[(S->r_i - 1 + WI_ROUNDS) % WI_ROUNDS];
      float p = wi_round_ptp(S, now - 60.0f);
      if (now >= S->P.warmup && p >= 0 && p < S->P.flat) wi_set_baseline(S, 60.0f);
      return 0;
    }
  }
  if (!S->has_base) return 0;
  if (S->base_ok[s] != (1u << WI_STEPS) - 1) return 0;
  {
    float d[WI_STEPS], mag; int lab;
    for (k = 0; k < WI_STEPS; k++) d[k] = x[k] - S->base[s][k];
    lab = wi_predict(&S->M, d, &mag);
    if (!((S->pred_flag >> s) & 1u)) { S->pred_ord[S->pred_n++] = (int8_t)s; S->pred_flag |= (uint8_t)(1u << s); }
    S->pred_lab[s] = lab; S->pred_mag[s] = mag;
    if (S->pred_n >= wi_count_bits(S->seen)) { wi_decide(S, t); decided = 1; }
  }
  return decided;
}

static inline void wi_hist_push(wi_state_t *S, int s, int k, float sec, float x) {
  uint8_t *hi = &S->hi[s][k];
  S->hx[s][k][*hi] = x; S->ht[s][k][*hi] = sec; *hi = (uint8_t)((*hi + 1) % WI_HIST);
  if (S->hn[s][k] < WI_HIST) S->hn[s][k]++;
}

/* 측정 행 1개. t_ms = 보드 millis, gas_ohm = 가스저항, k = Gas Index, valid = Gas Valid 비트.
   판정이 하나 나오면 1 을 돌려주고 S->last 에 결과가 있다. */
static inline int wi_feed(wi_state_t *S, double t_ms, int s, float gas_ohm, int k, int valid) {
  float sec, x;
  if (s < 0 || s >= WI_NS) return 0;
  if (!S->have_t0) { S->t0 = t_ms; S->have_t0 = 1; }
  else if (t_ms < S->last_t - 1000) {          /* 보드 리셋: 시간이 0 부터 다시 */
    int i; S->t0 += t_ms - S->last_t; S->resets++;
    for (i = 0; i < WI_NS; i++) { S->prev_k[i] = -1; S->got[i] = 0; S->tsum[i] = 0; S->tcnt[i] = 0; }
  }
  S->last_t = t_ms;
  sec = (float)((t_ms - S->t0) / 1000.0); S->sec_now = sec;
  if (!valid || gas_ohm <= 0 || k < 0 || k >= WI_STEPS) return 0;
  x = log10f(gas_ohm);
  S->seen |= (uint8_t)(1u << s);
  if (k < S->prev_k[s]) {                       /* 단계 번호가 되감김 = 이전 사이클 끝 */
    int done = (S->got[s] == (1u << WI_STEPS) - 1);
    float cx[WI_STEPS]; float ct = (float)(S->tsum[s] / (S->tcnt[s] ? S->tcnt[s] : 1));
    if (done) memcpy(cx, S->steps[s], sizeof(cx));
    S->got[s] = 0; S->tsum[s] = 0; S->tcnt[s] = 0;
    S->prev_k[s] = k; S->steps[s][k] = x; S->got[s] |= (uint16_t)(1u << k); S->tsum[s] += sec; S->tcnt[s]++;
    wi_hist_push(S, s, k, sec, x);
    return done ? wi_on_cycle(S, s, ct, cx) : 0;
  }
  S->prev_k[s] = k; S->steps[s][k] = x; S->got[s] |= (uint16_t)(1u << k); S->tsum[s] += sec; S->tcnt[s]++;
  wi_hist_push(S, s, k, sec, x);
  return 0;
}

/* ---------------------------------------------------------------- 모델 교체 (글자 형식) */
static inline uint32_t wi_crc32(const char *p, size_t n) {   /* zlib.crc32 과 같음 */
  uint32_t c = 0xFFFFFFFFu; size_t i; int b;
  for (i = 0; i < n; i++) {
    c ^= (uint8_t)p[i];
    for (b = 0; b < 8; b++) c = (c >> 1) ^ (0xEDB88320u & (0u - (c & 1u)));
  }
  return ~c;
}

static inline void wi_set_err(char *err, size_t errlen, const char *msg) {
  if (err && errlen) { strncpy(err, msg, errlen - 1); err[errlen - 1] = '\0'; }
}

/* 한 줄을 공백으로 나눈다 (최대 maxtok). 줄은 수정된다. */
static inline int wi_split(char *line, char **tok, int maxtok) {
  int n = 0; char *p = line;
  while (*p && n < maxtok) {
    while (*p == ' ' || *p == '\t' || *p == '\r') p++;
    if (!*p) break;
    tok[n++] = p;
    while (*p && *p != ' ' && *p != '\t' && *p != '\r') p++;
    if (*p) *p++ = '\0';
  }
  return n;
}

static inline int wi_parse_float(const char *s, float *out) {
  char *end; double v = strtod(s, &end);
  if (end == s || *end != '\0' || !isfinite(v)) return 0;
  *out = (float)v; return 1;
}

/* 글자 형식 모델 → out. 성공 1 / 실패 0 (err 에 이유). out 은 성공했을 때만 바뀐다. */
static inline int wi_model_parse(const char *txt, size_t len, wi_model_t *out, char *err, size_t errlen) {
  static char buf[WI_MODEL_TEXT_MAX + 1];
  wi_model_t M; const char *crcp; size_t body; unsigned long want; char *line, *next, *tok[16];
  int ntok, ver_ok = 0, tau_ok = 0, end_ok = 0, i, k;
  if (len == 0 || len > WI_MODEL_TEXT_MAX) { wi_set_err(err, errlen, "size"); return 0; }
  memcpy(buf, txt, len); buf[len] = '\0';
  /* CRC: 마지막 "crc " 줄 앞까지 */
  crcp = NULL;
  for (i = (int)len - 4; i >= 0; i--) if ((i == 0 || buf[i - 1] == '\n') && strncmp(buf + i, "crc ", 4) == 0) { crcp = buf + i; break; }
  if (!crcp) { wi_set_err(err, errlen, "no crc"); return 0; }
  body = (size_t)(crcp - buf);
  want = strtoul(crcp + 4, NULL, 16);
  if (wi_crc32(buf, body) != (uint32_t)want) { wi_set_err(err, errlen, "crc mismatch"); return 0; }
  buf[body] = '\0';
  memset(&M, 0, sizeof(M));
  for (line = buf; line && *line; line = next) {
    next = strchr(line, '\n'); if (next) *next++ = '\0';
    ntok = wi_split(line, tok, 16);
    if (ntok == 0) continue;
    if (strcmp(tok[0], "WARDMODEL") == 0) { if (ntok != 2 || strcmp(tok[1], "1") != 0) { wi_set_err(err, errlen, "version"); return 0; } ver_ok = 1; }
    else if (strcmp(tok[0], "id") == 0) {
      if (ntok != 2 || strlen(tok[1]) == 0 || strlen(tok[1]) >= WI_ID_LEN) { wi_set_err(err, errlen, "id"); return 0; }
      strcpy(M.id, tok[1]);
    }
    else if (strcmp(tok[0], "tau") == 0) {
      if (ntok != 3 || !wi_parse_float(tok[1], &M.tau_air) || !wi_parse_float(tok[2], &M.tau_decide)) { wi_set_err(err, errlen, "tau"); return 0; }
      if (!(M.tau_air > 0 && M.tau_air < 5 && M.tau_decide >= M.tau_air && M.tau_decide < 5)) { wi_set_err(err, errlen, "tau range"); return 0; }
      tau_ok = 1;
    }
    else if (strcmp(tok[0], "class") == 0) {
      wi_class_t *C; float norm = 0;
      if (ntok != 4 + WI_STEPS) { wi_set_err(err, errlen, "class fields"); return 0; }
      if (M.n >= WI_MAX_CLASSES) { wi_set_err(err, errlen, "too many classes"); return 0; }
      C = &M.c[M.n];
      if (strlen(tok[1]) == 0 || strlen(tok[1]) >= WI_NAME_LEN) { wi_set_err(err, errlen, "class name"); return 0; }
      strcpy(C->name, tok[1]);
      if (strcmp(tok[2], "0") != 0 && strcmp(tok[2], "1") != 0) { wi_set_err(err, errlen, "class on"); return 0; }
      C->on = (uint8_t)(tok[2][0] - '0');
      if (!wi_parse_float(tok[3], &C->angle_thr) || !(C->angle_thr > 0 && C->angle_thr <= 180)) { wi_set_err(err, errlen, "angle"); return 0; }
      for (k = 0; k < WI_STEPS; k++) {
        if (!wi_parse_float(tok[4 + k], &C->mu[k])) { wi_set_err(err, errlen, "mu"); return 0; }
        norm += C->mu[k] * C->mu[k];
      }
      if (fabsf(sqrtf(norm) - 1.0f) > 0.02f) { wi_set_err(err, errlen, "mu not unit"); return 0; }
      M.n++;
    }
    else if (strcmp(tok[0], "end") == 0) end_ok = 1;
    else { wi_set_err(err, errlen, "unknown line"); return 0; }
  }
  if (!ver_ok || !tau_ok || !end_ok || M.id[0] == '\0') { wi_set_err(err, errlen, "missing field"); return 0; }
  if (M.n < 1) { wi_set_err(err, errlen, "no class"); return 0; }
  *out = M;
  wi_set_err(err, errlen, "ok");
  return 1;
}
