/**
 * 센서(ESP32 BME688)가 MQTT 로 보내는 원래 라벨 → 대시보드 향 tone.
 *
 * 센서 펌웨어는 학습 모델의 클래스 이름을 그대로 보내는데, 이 이름이 실제 향과 어긋나 있다
 * (ward_bme688 학습 라벨 뒤바뀜). 센서를 다시 굽기 전까지 옛 라벨은 이 파일에서만 해석한다.
 *   "woody" · "citrus" → orange   "musk" → mint   "floral" · "lavender" · "odor" → lavender (시연에선 악취)
 * 새 이름(orange / mint / lavender)도 그대로 알아듣는다.
 */
const SENSOR_TONE_RULES = [
  ["fresh", /fresh\s*air|fresh|clean|무취|청정|(^|[^a-z])air([^a-z]|$)/i],
  ["orange", /orange|오렌지|woody|citrus/i],
  ["lavender", /lavender|라벤더|floral|odor|악취/i],
  ["mint", /mint|민트|musk/i],
];

export function sensorLabelTone(label) {
  const text = String(label ?? "");
  if (!text.trim()) return null;
  const rule = SENSOR_TONE_RULES.find(([, pattern]) => pattern.test(text));
  return rule ? rule[0] : null;
}
