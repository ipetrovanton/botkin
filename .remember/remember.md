# Handoff: feat/pipeline-speed-accuracy

**Ветка:** `feat/pipeline-speed-accuracy`

**Коммиты:**
- `56e6309` — long_side 1600, unit cleanup, early-exit voting
- `97ef735` — unit/ref swap + noise filter
- `6e045c0` — e2e doctor_report content (not only doc_type)

**E2E сейчас:** 34/34 PASS (analysis + doctor_report content), wall ~11.2 мин.

**doctor_report e2e:**
- hard: diagnosis, doctor_name, medications (recall≥0.5), wrong visit_date
- soft: recommendations, anamnesis, missing visit_date
- unknown 022/024/025: still doc_type only

**Следующее (опционально):**
- phase 9: Ollama num_ctx, multipage parallel
- e2e for unknown prescriptions (medications) if product wants
- push branch / PR
