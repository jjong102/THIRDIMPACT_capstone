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
 * 모델(model.h, tools/export_model.py 가 만듦): 클래스마다 평균 방향 10개 + 허용 각도, 문턱 2개.
 */
#pragma once
#include <math.h>
#include <stdint.h>
#include <string.h>

#define WI_NS     8      /* 센서 수 (DevKit) */
#define WI_STEPS  10     /* 히터 단계 수 */
#define WI_HIST   24     /* (센서, 단계)마다 기억할 최근 값 수 — 사이클당 1개, 약 4분 */
#define WI_ROUNDS 64     /* 평평한지 볼 라운드 기록 수 — 약 10분 */

typedef struct {
  const char *name;
  uint8_t     on;                 /* 1 = 악취(출동) */
  float       angle_thr;          /* 허용 각도(도) */
  float       mu[WI_STEPS];       /* 평균 방향(단위벡터) */
} wi_class_t;

#include "model.h"

/* 판정 라벨: 0..N-1 = 클래스, 음수 = 특수 */
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
} wi_params_t;

static inline wi_params_t wi_default_params(void) {
  wi_params_t p = {3, 12, 180.0f, 60.0f, 0.05f, 0.02f, 180.0f, 0, 180.0f};
  return p;
}

typedef struct {                  /* 한 번의 판정(라운드) 결과 */
  float sec;                      /* 녹화(부팅) 시작 기준 초 */
  int   top;                      /* 표결 결과 라벨 */
  int   k, n;                     /* 표 수 / 센서 수 */
  float mag;                      /* 센서별 |Δ| 중앙값 */
  int   purifier;                 /* 권고 상태 1 = ON */
  int   event;                    /* 0 없음, 1 = 방금 ON, 2 = 방금 OFF */
  int   rebased;                  /* 1 = 이번에 기준선을 다시 잡음 */
} wi_decision_t;

typedef struct {
  wi_params_t P;
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
  int    purifier; float on_since; int run_on, run_off, air_run, calm_run; float lock_until;
  wi_decision_t last;
  int    n_decisions;
} wi_state_t;

/* ---------------------------------------------------------------- 도우미 */
static inline const char *wi_label_name(int lab) {
  if (lab >= 0 && lab < WI_N_CLASSES) return WI_CLASSES[lab].name;
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
static inline int wi_predict(const float *d, float *mag_out) {
  int k, c, best = -1; float n = 0, bang = 1e9f;
  for (k = 0; k < WI_STEPS; k++) { if (!isfinite(d[k])) { *mag_out = NAN; return WI_NONE; } n += d[k] * d[k]; }
  n = sqrtf(n); *mag_out = n;
  if (n < WI_TAU_AIR) return WI_AIR;
  if (n < WI_TAU_DECIDE) return WI_WEAK;
  for (c = 0; c < WI_N_CLASSES; c++) {
    float dot = 0, a;
    for (k = 0; k < WI_STEPS; k++) dot += (d[k] / n) * WI_CLASSES[c].mu[k];
    if (dot > 1) dot = 1;
    if (dot < -1) dot = -1;
    a = acosf(dot) * 57.29577951f;
    if (a < bang) { bang = a; best = c; }
  }
  if (best < 0) return WI_UNKNOWN;
  return bang <= WI_CLASSES[best].angle_thr ? best : WI_UNKNOWN;
}

static inline int wi_is_on(int lab) { return lab == WI_UNKNOWN || (lab >= 0 && WI_CLASSES[lab].on); }

/* open_set.vote: 과반이면 그 라벨, 아니면 '끔' 쪽 향(Air 제외)이 합쳐 과반이면 그중 최다, 아니면 Unknown.
   동률은 먼저 나온 라벨 (파이썬 Counter.most_common 과 같음) */
static inline int wi_vote(const int *labs, int n, int *k_out) {
  int i, j, top = WI_NONE, topc = 0, off = 0, offtop = WI_NONE, offc = 0;
  for (i = 0; i < n; i++) {
    int c = 0; for (j = 0; j < n; j++) if (labs[j] == labs[i]) c++;
    if (c > topc) { topc = c; top = labs[i]; }
  }
  if (!n) { *k_out = 0; return WI_NONE; }
  if (topc * 2 > n) { *k_out = topc; return top; }
  for (i = 0; i < n; i++) if (labs[i] >= 0 && !WI_CLASSES[labs[i]].on) off++;
  if (off * 2 > n) {
    for (i = 0; i < n; i++) {
      int c = 0; if (!(labs[i] >= 0 && !WI_CLASSES[labs[i]].on)) continue;
      for (j = 0; j < n; j++) if (labs[j] == labs[i]) c++;
      if (c > offc) { offc = c; offtop = labs[i]; }
    }
    *k_out = off; return offtop;
  }
  *k_out = topc; return WI_UNKNOWN;
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
  top = wi_vote(labs, n, &k);
  want = (top == WI_WEAK || top == WI_NONE) ? -1 : wi_is_on(top);
  if (want == 1 && top == WI_UNKNOWN && sec < S->lock_until) want = -1;
  S->air_run = (top == WI_AIR) ? S->air_run + 1 : 0;
  if (want == 1) { S->run_on++; S->run_off = 0; }
  else if (want == 0) { S->run_on = 0; S->run_off++; }
  D.event = 0;
  if (!S->purifier && S->run_on >= S->P.on_cycles) { S->purifier = 1; S->on_since = sec; D.event = 1; }
  else if (S->purifier && S->run_off >= S->P.off_cycles && sec - S->on_since >= S->P.min_on) { S->purifier = 0; D.event = 2; }
  /* 기준선 추종: Air 가 6사이클 이상, 변화량이 문턱 절반 아래일 때만 아주 조금 */
  if (S->air_run >= 6 && med <= WI_TAU_AIR / 2) {
    int s; float m, a = S->P.track;
    for (s = 0; s < WI_NS; s++) for (k = 0; k < WI_STEPS; k++)
      if ((S->base_ok[s] >> k) & 1u)
        if (wi_recent_mean(S, s, k, S->sec_now - 60.0f, &m)) S->base[s][k] = (1 - a) * S->base[s][k] + a * m;
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
  D.sec = sec; D.top = top; D.k = k; D.n = n; D.mag = med; D.purifier = S->purifier;
  S->last = D; S->n_decisions++;
}

/* ---------------------------------------------------------------- 입력 */
static inline void wi_init(wi_state_t *S, wi_params_t P) {
  int s; memset(S, 0, sizeof(*S)); S->P = P; S->lock_until = -1.0f;
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
    lab = wi_predict(d, &mag);
    if (!((S->pred_flag >> s) & 1u)) { S->pred_ord[S->pred_n++] = (int8_t)s; S->pred_flag |= (uint8_t)(1u << s); }
    S->pred_lab[s] = lab; S->pred_mag[s] = mag;
    if (S->pred_n >= wi_count_bits(S->seen)) { wi_decide(S, t); decided = 1; }
  }
  return decided;
}

/* 측정 행 1개. t_ms = 보드 millis, gas_ohm = 가스저항, k = Gas Index, valid = Gas Valid 비트.
   판정이 하나 나오면 1 을 돌려주고 S->last 에 결과가 있다. */
static inline int wi_feed(wi_state_t *S, double t_ms, int s, float gas_ohm, int k, int valid) {
  float sec, x; int decided = 0;
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
    { uint8_t *hi = &S->hi[s][k]; S->hx[s][k][*hi] = x; S->ht[s][k][*hi] = sec; *hi = (uint8_t)((*hi + 1) % WI_HIST);
      if (S->hn[s][k] < WI_HIST) S->hn[s][k]++; }
    if (done) decided = wi_on_cycle(S, s, ct, cx);
    return decided;
  }
  S->prev_k[s] = k; S->steps[s][k] = x; S->got[s] |= (uint16_t)(1u << k); S->tsum[s] += sec; S->tcnt[s]++;
  { uint8_t *hi = &S->hi[s][k]; S->hx[s][k][*hi] = x; S->ht[s][k][*hi] = sec; *hi = (uint8_t)((*hi + 1) % WI_HIST);
    if (S->hn[s][k] < WI_HIST) S->hn[s][k]++; }
  return 0;
}
