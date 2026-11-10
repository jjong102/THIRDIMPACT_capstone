# 문서 모음 (WARD 캡스톤)

프로젝트의 모든 `.md` 문서를 한곳에서 볼 수 있는 인덱스입니다.
순수 문서는 `docs/`에 모았고, 코드에 딸린 문서(git README·스케치 가이드)는 제자리에 두고 링크로 연결했습니다.

## 기획 · 설계 (docs/)

| 문서 | 내용 |
|------|------|
| [PLAN.md](PLAN.md) | 다중 클래스 향기 분류 전체 계획 — raw 가스저항 → Python(scikit-learn) 직접 학습, 테스트 플랜(T1~T7), 품질 필터 근거 |
| [향기학습_계획서_팀공유용.md](scent_training_plan_team.md) | 팀 공유용 쉬운 설명 — AI-Studio 4클래스 제한 우회, 소믈리에 비유, 역할 분담 |
| [발향부_설계.md](diffuser_design.md) | 발향부 하드웨어 설계 — 서보 + 향수 펌프 공병(원액 분사), BOM, 기구 설계 |

## 펌웨어 문서 (docs/)

| 문서 | 내용 |
|------|------|
| [펌웨어_정리_노션.md](firmware_notes.md) | 전체 펌웨어 정리 — 4개 스케치의 역할, 히터 프로파일 불변식, 추론 방식 비교, 교훈 |
| [수집_추론_가이드.md](collection_inference_guide.md) | 데이터 수집 → 추론 상세 운영 가이드 |
| [수집_체크리스트.md](collection_checklist.md) | 재수집 체크리스트 — 목표 클래스(Air/Good/Bad), 세션 규칙 |

## 코드에 딸린 문서

| 문서 | 위치 | 내용 |
|------|------|------|
| [ward_bme688 README](../README_bsec_classifier.md) | `ward_bme688/` | ESP32 → MQTT(HiveMQ) 실시간 분류 (bme688_sel_esp32 + lib + receiver) |
| [my_bme688 Quick Start](../firmware/my_bme688/Quick_Start_Guide.md) | `firmware/my_bme688/` | my_bme688 스케치 빠른 시작 가이드 |

> BME688 공식 데이터시트(bst-bme688-ds000.pdf)는 Bosch 저작물이라 저장소에는 포함하지 않았습니다 — Bosch Sensortec 사이트에서 내려받으세요.
