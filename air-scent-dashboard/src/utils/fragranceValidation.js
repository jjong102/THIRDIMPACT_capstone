import { getRecipeById } from "../data/scentRecipes";
import { blendToWeights, LEVEL_UNIT_MS, MIST_CHANNELS } from "./fragranceCommand";

/** 최대 작동 시간(초) */
export const MAX_DURATION_SECONDS = 60;

/** 1회 연속 분사 상한(초) */
export const MAX_CONTINUOUS_SPRAY_SECONDS = 30;

/** 분사 간 최소 쿨다운(초) */
export const MIN_COOLDOWN_SECONDS = 120;

/** 채널별 최소 잔량(%) — 이하면 해당 채널 사용 레시피 불가 */
export const MIN_CARTRIDGE_PERCENT = 10;

/**
 * 사용 불가능한 향 조합
 * key: 레시피 ID, value: 차단 조건 (센서/상태 기반)
 */
export const FORBIDDEN_COMBINATIONS = [
  {
    recipeIds: ["R003"],
    when: (ctx) => ctx.bme688?.humidity != null && ctx.bme688.humidity < 30,
    reason: "습도가 너무 낮아 오렌지 향은 사용할 수 없습니다.",
  },
];

function estimateSpraySeconds(blend, intensity, durationSeconds) {
  const weights = blendToWeights(blend);
  const unitMs = LEVEL_UNIT_MS[intensity] ?? LEVEL_UNIT_MS[2];
  const maxWeight = Math.max(...weights.split("").map(Number));
  const maxChannelSeconds = (maxWeight * unitMs) / 1000;
  return Math.min(durationSeconds, maxChannelSeconds * 3);
}

/**
 * @returns {{ valid: boolean, errors: string[], warnings: string[], adjustedDuration: number }}
 */
export function validateDispenseRequest({
  recipeId,
  recipe_id,
  intensity,
  durationSeconds,
  duration_seconds,
  cartridgeRemaining,
  lastDispenseAt,
  context = {},
}) {
  const errors = [];
  const warnings = [];
  const resolvedRecipeId = recipeId ?? recipe_id;
  const resolvedDuration = durationSeconds ?? duration_seconds;

  const recipe = getRecipeById(resolvedRecipeId);
  if (!recipe) {
    return {
      valid: false,
      errors: [`등록되지 않은 레시피 ID: ${resolvedRecipeId}`],
      warnings: [],
      adjustedDuration: 0,
    };
  }

  const level = Math.min(3, Math.max(1, Number(intensity) || 2));
  let duration = Math.min(
    MAX_DURATION_SECONDS,
    Math.max(5, Number(resolvedDuration) || 15)
  );

  if (duration > MAX_CONTINUOUS_SPRAY_SECONDS) {
    warnings.push(
      `연속 분사 시간 ${duration}초가 상한(${MAX_CONTINUOUS_SPRAY_SECONDS}초)을 초과하여 조정됩니다.`
    );
    duration = MAX_CONTINUOUS_SPRAY_SECONDS;
  }

  if (lastDispenseAt) {
    const elapsed = (Date.now() - new Date(lastDispenseAt).getTime()) / 1000;
    if (elapsed < MIN_COOLDOWN_SECONDS) {
      errors.push(
        `쿨다운 중입니다. ${Math.ceil(MIN_COOLDOWN_SECONDS - elapsed)}초 후 다시 시도해 주세요.`
      );
    }
  }

  for (const rule of FORBIDDEN_COMBINATIONS) {
    if (rule.recipeIds.includes(resolvedRecipeId) && rule.when(context)) {
      errors.push(rule.reason);
    }
  }

  for (const channel of MIST_CHANNELS) {
    const share = recipe.blend[channel] ?? 0;
    if (share <= 0) {
      continue;
    }

    const remaining = cartridgeRemaining?.[channel];
    if (remaining != null && remaining < MIN_CARTRIDGE_PERCENT) {
      errors.push(
        `${channel} 카트리지 잔량(${remaining}%)이 부족하여 이 레시피를 사용할 수 없습니다.`
      );
    }
  }

  const estimated = estimateSpraySeconds(recipe.blend, level, duration);
  if (estimated < duration) {
    warnings.push(`펌웨어 기준 실제 분사 시간은 약 ${estimated.toFixed(1)}초입니다.`);
  }

  return {
    valid: errors.length === 0,
    errors,
    warnings,
    adjustedDuration: duration,
    recipe,
    intensity: level,
  };
}
