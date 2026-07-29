# Handoff: правка expected.json завершена

**Цель:** исправить все рассинхроны sample vs expected по результатам ручной валидации.

**Сделано (2026-07-28, feat/email-auth):**
- Обновлены 15 sidecar: 003, 010, 021–025, 028–035.
- PDF: 003 (+Токсокары, ЦИК описторхов), 010 (полный ОАМ).
- Критические doctor_report/рецепты переписаны по бланкам.
- sample_025: имя препарата (Terbinafini) — best-effort по рукописи; D.t.d. N 60 и схема 1×2 / 21 день читаются уверенно.

**Следующий шаг (по желанию):**
- Прогнать e2e llm: `wsl -d Ubuntu -- .venv/Scripts/python.exe -m pytest tests/test_e2e_llm.py -m llm -s --tb=short`
- Пользователь может перепроверить рукопись sample_025 (Terbinafini vs иное).
- Закоммитить expected при approve.

**Не закоммичено** — ждать слова пользователя.
