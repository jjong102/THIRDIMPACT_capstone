/** 미리 등록된 향 레시피 — AI 추천은 Mint / Lavender / Orange만 선택 */
export const SCENT_RECIPES = [
  {
    id: "R001",
    name: "Mint",
    displayName: "Mint",
    blend: { mint: 100, lavender: 0, orange: 0 },
    tags: ["Mint", "페퍼민트", "민트", "시원", "상쾌", "집중"],
    description: "Peppermint scent for a cool, clear head.",
  },
  {
    id: "R002",
    name: "Lavender",
    displayName: "Lavender",
    blend: { mint: 0, lavender: 100, orange: 0 },
    tags: ["Lavender", "라벤더", "휴식", "수면", "편안", "스트레스"],
    description: "Lavender scent to unwind and rest.",
  },
  {
    id: "R003",
    name: "Orange",
    displayName: "Orange",
    blend: { mint: 0, lavender: 0, orange: 100 },
    tags: ["Orange", "오렌지", "상큼", "활력", "기분 전환", "깨끗"],
    description: "Orange scent for a bright lift.",
  },
];

/** 화면 표시 이름. 내부 ID(Mint/Lavender/Orange)는 저장값·AI·센서 연동에 그대로 쓴다. */
export const SCENT_LABELS = {
  Mint: { en: "Peppermint", ko: "페퍼민트" },
  Lavender: { en: "Lavender", ko: "라벤더" },
  Orange: { en: "Orange", ko: "오렌지" },
};

/** 향 ID → 화면 이름. 향 ID가 아니면(Fresh Air, odor 등) 그대로 돌려준다. */
export function scentLabel(name, lang = "en") {
  const text = String(name ?? "").trim();
  return SCENT_LABELS[text]?.[lang] ?? text;
}

/** AI 추천 이유처럼 향 ID가 섞인 문장을 화면 이름으로 바꾼다. */
export function localizeScentText(text) {
  return String(text ?? "")
    .replace(/\bMint\b/g, SCENT_LABELS.Mint.en)
    // 화면 이름은 모두 받침 없이 끝나므로 조사를 맞춘다 (Mint이 → Peppermint가)
    .replace(
      /(Peppermint|Lavender|Orange|페퍼민트|라벤더|오렌지)(이에요|으로|이|을|은|과)(?=[\s.,!?·]|$)/g,
      (_, name, particle) => name + { 이에요: "예요", 으로: "로", 이: "가", 을: "를", 은: "는", 과: "와" }[particle],
    );
}

export const SCENT_CHOICES = SCENT_RECIPES.map((recipe) => ({
  id: recipe.id,
  label: scentLabel(recipe.displayName),
}));

export function getRecipeById(recipeId) {
  return SCENT_RECIPES.find((recipe) => recipe.id === recipeId) ?? null;
}

export function getRecipeByName(name) {
  const display = scentDisplayName(name);
  return (
    SCENT_RECIPES.find((recipe) => recipe.displayName === display) ??
    SCENT_RECIPES[0]
  );
}

export function blendForFragrance(name) {
  return { ...getRecipeByName(name).blend };
}

export function channelsForFragrance(name) {
  const blend = blendForFragrance(name);
  return {
    mint: blend.mint > 0,
    lavender: blend.lavender > 0,
    orange: blend.orange > 0,
  };
}

/** 향 ID·화면 이름·한글 이름 → 향 ID. 향이 아니면 null. */
export function scentIdFromText(name) {
  const text = String(name ?? "").trim().toLowerCase();
  if (!text) return null;
  if (/lavender|라벤더/.test(text)) return "Lavender";
  if (/orange|오렌지/.test(text)) return "Orange";
  if (/mint|민트/.test(text)) return "Mint";
  return null;
}

export function scentDisplayName(name) {
  const text = String(name || "").trim();
  return scentIdFromText(text) ?? (text || "Mint");
}

/** 레시피별 시각적 향 무드 (결과 화면 이모티콘) */
export const RECIPE_VISUALS = {
  R001: { emojis: ["🌿", "🍃", "✨"], mood: SCENT_LABELS.Mint.en },
  R002: { emojis: ["💜", "🌸", "😌"], mood: SCENT_LABELS.Lavender.en },
  R003: { emojis: ["🍊", "🧡", "💫"], mood: SCENT_LABELS.Orange.en },
};

export function getRecipeVisuals(recipeId) {
  return (
    RECIPE_VISUALS[recipeId] ?? {
      emojis: ["✨", "🌸", "🍃"],
      mood: "Scent",
    }
  );
}
