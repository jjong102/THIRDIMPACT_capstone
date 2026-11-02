import { ArrowLeft, SprayCan } from "lucide-react";

import { SCENT_RECIPES, getRecipeVisuals, scentDisplayName, scentLabel } from "../data/scentRecipes";
import "./FragranceBlendPage.css";

function recipeFromScent(fragrance, blend) {
  const name = scentDisplayName(fragrance);
  const byName = SCENT_RECIPES.find((recipe) => recipe.displayName === name);
  if (byName) return byName;

  const mint = Number(blend?.mint) || 0;
  const lavender = Number(blend?.lavender) || 0;
  const orange = Number(blend?.orange) || 0;
  if (lavender >= mint && lavender >= orange) {
    return SCENT_RECIPES.find((recipe) => recipe.id === "R002") ?? SCENT_RECIPES[0];
  }
  if (orange >= mint && orange >= lavender) {
    return SCENT_RECIPES.find((recipe) => recipe.id === "R003") ?? SCENT_RECIPES[0];
  }
  return SCENT_RECIPES[0];
}

function FragranceBlendPage({
  selectedFragrance,
  fragranceBlend,
  onSelect,
  onBack,
}) {
  const selected = recipeFromScent(selectedFragrance, fragranceBlend);

  return (
    <section className="fragrance-blend-page">
      <article className="fragrance-blend-card">
        <header className="fragrance-blend-header">
          <button
            className="fragrance-blend-back"
            type="button"
            onClick={onBack}
          >
            <ArrowLeft size={22} />
            <span>돌아가기</span>
          </button>

          <div className="fragrance-blend-header-title">
            <SprayCan size={22} />
            <strong>향기 선택</strong>
          </div>

          <span className="fragrance-blend-total">{scentLabel(selected.displayName)}</span>
        </header>

        <div className="fragrance-blend-body">
          <p className="fragrance-blend-guide">
            이 집에 뿌릴 향기를 골라 주세요. Peppermint, Lavender, Orange 중 하나를
            선택할 수 있습니다.
          </p>

          <ul className="fragrance-select-list">
            {SCENT_RECIPES.map((recipe) => {
              const visual = getRecipeVisuals(recipe.id);
              const active = recipe.id === selected.id;

              return (
                <li key={recipe.id}>
                  <button
                    type="button"
                    className={`fragrance-select-card ${active ? "is-selected" : ""}`}
                    onClick={() => onSelect?.(recipe)}
                    aria-pressed={active}
                  >
                    <span className="fragrance-select-emojis" aria-hidden="true">
                      {visual.emojis.map((emoji) => (
                        <em key={emoji}>{emoji}</em>
                      ))}
                    </span>
                    <span className="fragrance-select-copy">
                      <strong>{scentLabel(recipe.displayName)}</strong>
                      <span>{recipe.description}</span>
                    </span>
                    {active ? (
                      <span className="fragrance-select-badge">선택됨</span>
                    ) : null}
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      </article>
    </section>
  );
}

export default FragranceBlendPage;
