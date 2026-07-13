Here's a thinking process:

1.  **Analyze User Input & Constraints:**
    - **Role:** Experienced medical AI analyst.
    - **Task:** Create a detailed, structured health report based on provided data.
    - **Rules:**
        1. ONLY Russian. No Ukrainian letters (і, ї, є, ґ). Only standard medical abbreviations in English allowed.
        2. Do not refuse or add disclaimers like "I'm not a doctor".
        3. Rely strictly on provided data: labs, doctor conclusions, Garmin metrics. Quote specific values.
        4. Use "СВЕЖИЕ ИССЛЕДОВАНИЯ" block if present, cite references.
        5. Do not invent lab values or medications not in the data.
        6. Structure with `##` headings for each section.
        7. Be specific in recommendations: dosages, duration, frequency, when to repeat tests.
        8. Assess disease risks based on deviations and lifestyle.
        9. Note contradictions if any (e.g., different units).
    - **Data Provided:**
        - Patient: Male, active Garmin user. Period: May 2025 – July 2026. Sources: labs, neurologist/therapist conclusions, Garmin Connect.
        - Labs: Extensive table with many duplicates/synonyms (BUN, CRP, CD31+, Creatinine, Globulins, MCH/MCV/RDW, Total Protein, Albumin/Creatinine ratio?, Basophils, pH urine, Hematocrit, Hemoglobin, WBC, Lymphocytes, Monocytes, Uric Acid, Neutrophils, Specific gravity urine, ESR, Reticulocyte indices, Platelets, Eosinophils, Squamous epithelium in urine). Most are marked "в норме" (normal), some ↑ or ↓.
        - Doctor Conclusions: 8 conclusions from May 2025 to Mar 2026. Main diagnoses revolve around autonomic dysfunction (G90.8/G90.9), asthenic syndrome, cognitive symptoms, hypertension/hypotension mentions, recurrent depression. Medications: Fluoxetine, Trittico (agomelatine?), Gliatilin/Elkar/Brainmax/Stimol/Brintellix/Adaptol/Mebicar. Note spelling variations in drug names due to OCR/transcription errors in the prompt (Флюоксетин Ланнакер -> Флуоксетин, Тритико -> Агомелатин? Actually Trittico is Agomelatine brand name in Russia/CIS), Глиалатин/Глипатили/Глиатилин -> Gliatilin (Citicoline), Элкап/Эллкар/Elkar -> L-carnitine, Бринтелликс -> Vortioxetine, Стимол -> Stimol (likely a nootropic/adaptogen mix or similar), Брейнмакс -> Brainmax. I will stick to the names given but clarify standard equivalents if needed, without inventing new ones.
        - Garmin Data: Body Battery avg 63.2, Resting HR avg 60.2, Steps avg ~5610, Stress avg 36.1, Nightly HRV avg 32.7 ms, Sleep avg 7.5h. Activities: running (6x), swimming (5x), gravel cycling (3x), walking (2x).
        - Fresh Research: Mentions Fluoxetine registry status "expired" in GRLS/FSLI. PubMed article on methemoglobinemia/CBC abnormalities (not directly applicable to this patient's normal CBC, but I'll note it if relevant or skip as per rule 4/5). Actually, the prompt says "Если в контексте есть блок «СВЕЖИЕ ИССЛЕДДОВАНИЯ» — используй его, указывай ссылки." I will briefly mention the Fluoxetine registry status and the CBC article context.
    - **Required Sections:** Exactly 12 sections as listed, using `##` headings.

2.  **Data Processing & Validation (Mental Walkthrough):**
    - *Labs:* Most are normal. Key deviations: Lymphocytes % ↑ (40% vs ref 19-37), Monocytes % ↑ (11.7% vs ref 3-11), Neutrophils % ↓ (44.6% vs ref 48-78). Absolute counts for lymphs/monos/neuts are normal or borderline, indicating relative shifts rather than absolute pathology. CRP is mostly normal (1.8-3.06 mg/L), one value says ↑ above norm but still low. Uric acid: 581.09 μmol/L marked "в норме" in table despite ref saying 220-450? Wait, the table says `Мочевая кислота | 581.09 | мкмоль/л | 220 - 450 umol/L | в норме`. This is a contradiction per rule 9. I must note it: value exceeds reference range but marked normal; likely lab-specific or requires verification. Creatinine: 1.781 mg/dL vs ref 0.8-1.2, marked "в norme". Another contradiction. Total Protein: 99 g/L vs ref 60-80, marked normal. Globulins: 51 g/L vs ref 0-20 (likely a typo in reference range provided, should be ~20-35 or similar), marked normal. I will explicitly flag these contradictions per rule 9.
    - *Doctors:* Consistent theme of autonomic dysfunction, asthenia, cognitive symptoms, depression/anxiety. Medications focus on SSRIs (Fluoxetine 20mg), Agomelatine/Trittico (~100-150mg at night), Citicoline/Gliatilin, L-carnitine/Elkar, Vortioxetine/Brintellix later. Recommendations emphasize sleep 7-8h, daily activity (walking/swimming), stress management.
    - *Garmin:* Avg steps ~5610 (below recommended 7k-10k for optimal health). Sleep avg 7.5h aligns with doctor advice but has wide range (4.2 to 10.8h). HRV avg 32.7 ms is moderate/low-moderate, suggests autonomic load/stress recovery needs attention. Resting HR ~60 bpm is good for active male. Body Battery avg 63.2 indicates decent but fluctuating energy reserves. Stress score 36.1 is low-moderate. Activities show mixed modalities with high calorie burn (swimming shows unusually high kcal: 1258 per session? Might be a data artifact or includes long duration, I'll note it).
    - *Fresh Research:* Fluoxetine status "expired" in registry -> implies need to verify current prescription/availability. PubMed article on CBC puzzles/methemoglobinemia -> not directly applicable since patient's RBC indices are normal, but confirms importance of differential analysis interpretation (relative vs absolute counts).

3.  **Draft Construction - Section by Section (Mental Refinement):**
    *Rule Check:* Only Russian, no Ukrainian letters, `##` headings, specific data quotes, contradictions noted, risks assessed, recommendations concrete.

    ## 1. Общая оценка здоровья
    Сводный вывод: Пациент демонстрирует стабильное базовое состояние с преобладанием функциональных расстройств вегетативной нервной системы и астенического спектра. Лабораторные показатели крови в основном укладываются в физиологические границы, однако выявлены относительные смещения лейкоцитарной формулы (лимфоцитоз 40%, моноцитоз 11,7% при нейтропении 44,6%). Данные Garmin указывают на умеренную физическую активность и переменное качество восстановления. Клиническая картина соответствует диагнозу соматоформной вегетативной дисфункции с тревожно-астеническим синдромом и элементами когнитивных нарушений. Прогноз благоприятный при соблюдении режима, коррекции медикаментозной терапии и мониторинге метаболических маркеров.

    ## 2. Анализ лабораторных результатов
    - **Кровь (ОАК):** Эритроцитарные индексы стабильны: Hb 137–146 г/л, MCV 87,9 фл, RDW 11,9%, тромбоциты 217×10⁹/л. Анемия или дефицит железа исключены.
    - **Лейкоцитарная формула:** Относительный лимфоцитоз (40% при реф. 19–37%) и моноцитоз (11,7% при реф. 3–11%), сопровождающиеся относительной нейтропенией (44,6% при реф. 48–78%). Абсолютные значения в норме (лимфоциты ~2,06×10⁹/л, моноциты 0,6×10⁹/л, нейтрофилы 2,3×10⁹/л), что указывает на физиологическую перестройку или адаптацию к хронической нагрузке/стрессу, а не на инфекционный процесс.
    - **Воспаление и белки:** CRP в пределах нормы (1,8–3,06 мг/л). СОЭ 9 мм/ч. Общий белок крови указан как 99 г/л при референсе до 80 г/л — отмечено противоречие: значение превышает диапазон, однако статус помечен «в норме». Глобулины 51 г/л также выше указанного реф. диапазона (до 20 г/л), что требует уточнения лабораторных нормативов или пересчета фракций.
    - **Почки и метаболизм:** Креатинин 1,781 мг/дл при реф. 0,8–1,2 мг/дл — противоречие в статусе «в норме». При активной физической нагрузке (бег, плавание) уровень может соответствовать физиологической гиперкреатинемии за счет мышечной массы, но требует контроля СКФ и соотношения альбумин/креатинин. Мочевая кислота 581,09 мкмоль/л при реф. до 450 — аналогичное противоречие в маркировке; значение указывает на гиперурикемию, требующую внимания к диете и гидратации.
    - **Протививоречия:** Разные единицы измерения для гемоглобина (г/л vs г/дл) корректно пересчитаны лабораторией. Отметка «в норме» у креатинина, общего белка и мочевой кислоты при выходе за референсные диапазоны требует верификации с учетом поправочных коэффициентов лаборатории или функционального резерва почек.

    ## 3. Анализ заключений врачей
    - **Сопоставление диагнозов:** В мае–июне 2025 г. диагностирована вегетативная дисфункция (G90.8/G90.9) с астено-невротическим и цереброастеническим синдромами, тревожной симптоматикой. К марту 2026 г. картина дополнена умеренной надсегментарной недостаточностью, рекуррентной депрессией и артериальной гипотонией/гипертонической реактивностью. Это отражает естественную динамику соматоформного расстройства с переходом в более стабильное состояние («улучшение» указано в июньских заключениях).
    - **Адекватность назначений:** Схема построена на комбинированном подходе: антидепрессивная терапия (Флуоксетин 20 мг утром, Тритико/агомелатин ~100–150 мг вечером), ноотропная и метаболическая поддержка (Глиатилин/цитиколин 600 мг, Элькар/L-карнитин по 3 мл). В марте 2026 г. добавлены Бринтелликс (вортиоксетин) 20 мг длительно, Брейнмакс и Стимол для усиления когнитивного эффекта. Назначения соответствуют клиническим рекомендациям при тревожно-депрессивных расстройствах с астенией.
    - **Противоречия/Особенности:** Многократное дублирование заключений от 03.06.2025 связано, вероятно, с оптическим распознаванием текста (разные написание: Флюоксетин Ланнакер → Флуоксетин, Тритико → агомелатин). Дозировка Тритико корректируется до 100 мг в вечернее время для улучшения сна. Переход с монотерапии на комбинированный прием (Флуоксетин + Бринтелликс) требует контроля со стороны невролога/психиатра из-за риска серотониновой нагрузки, хотя препараты имеют разные механизмы действия.

    ## 4. Анализ данных Garmin
    - **Восстановление и HRV:** Средний ночной HRV составляет 32,7 мс (диапазон 20–45). Значение находится в нижней трети нормы для активного мужчины, что коррелирует с диагнозом вегетативной дисфункции и указывает на преобладание симпатического тонуса или недостаточную парасимпатическую активацию во время сна.
    - **Сон:** Средняя продолжительность 7,5 ч (минимум 4,2 ч, максимум 10,8 ч). Разброс свидетельствует о нестабильном режиме. Дни с коротким сном (<6 ч) совпадают со снижением Body Battery до минимума в 16 баллов.
    - **Стресс и активность:** Средний стресс по оценкам устройства — 36,1 балл (умеренный). Пульс покоя стабилен на уровне ~60 уд/мин, что отражает хорошую базовую кардиореспираторную выносливость. Средняя дневная норма шагов составляет 5610, что ниже оптимального порога в 7000–8000 для поддержания метаболического здоровья и вегетативной регуляции.
    - **Тренды нагрузок:** Бег (средний пульс ~144), плавание (~128) и гравийный велосипед демонстрируют разнообразие кардионагрузок. Расход калорий при плавании указан как 1258 ккал за сессию, что может быть артефактом расчета устройства или отражать высокую интенсивность/длительность (67 мин).

    ## 5. Оценка рисков
    - **Сердечно-сосудистые:** Риск умеренный. Стабильный ЧСС покоя и нормальный гематокрит снижают вероятность острых событий, однако вегетативная лабильность и эпизоды гипотонии/гипертонической реактивности повышают риск ортостатических нарушений при резких изменениях положения тела.
    - **Метаболические:** Выявлена тенденция к гиперурикемии (мочевая кислота 581,09 мкмоль/л) и повышенному креатинину (1,781 мг/дл). При отсутствии симптомов это указывает на риск формирования подагры или снижения клубочковой фильтрации в отдаленном периоде. Требуется контроль гидратации и ограничения пуринов.
    - **Психические:** Высокий риск рецидива астенического синдрома и тревожно-депрессивных эпизодов при нарушении режима сна (<7 ч) или перетренированности (снижение HRV <25 мс). Текущая медикаментозная поддержка снижает этот риск, но требует плавной коррекции доз.
    - **Иммунные/Воспалительные:** Относительный лимфоцитоз и моноцитоз на фоне нормального CRP свидетельствуют о хроническом низкоинтенсивном воспалении или адаптации к стрессу, без риска острых инфекционных осложнений.

    ## 6. Рекомендации по образу жизни
    - **Сон:** Строго фиксировать время отбоя на 23:00–23:30 и подъема в 7:00–7:30 для достижения целевых 8 часов. Избегать «отсыпаний» выходного дня более чем на 1 час во избежание десинхронизации циркадных ритмов.
    - **Стресс-менеджмент:** При падении HRV ниже 25 мс или Body Battery <40 баллов сокращать интенсивность тренировок в этот день до восстановительной ходьбы (30 мин). Вводить ежедневные дыхательные практики по схеме «квадратное дыхание» (вдох-задержка-выдох-задержка по 4 сек) перед сном.
    - **Режим дня:** Разделять когнитивную и физическую нагрузку. Утренние часы выделять под интеллектуальную работу, дневные — под активность. Контрастный душ утром (разница температур не более 5–7 °C для предотвращения сосудистого спазма) и теплый вечерний душ соответствуют назначениям невролога.
    - **Гидратация:** Увеличить потребление воды до 2 л/сутки равномерно в течение дня, особенно при физических нагрузках, для коррекции показателей мочи (pH 5,5 указывает на умеренную ацидификацию) и выведения пуринов.

    ## 7. Рекомендации по медикаментам
    - **Текущая схема:** Флуоксетин 20 мг утром + Тритико ~100–150 мг вечером обеспечивают базовый антидепрессивный и снотворный эффект. Добавление Бринтелликс 20 мг усиливает когнитивную поддержку. Курс Глиатилина (600 мг) и Элькар по 3 мл утром направлен на метаболическую реабилитацию ЦНС.
    - **Корректировки:** При стабильном самочувствии через 1–2 месяца обсудить с неврологом возможность монитора серотониновой нагрузки при комбинировании Флуоксетина и Бринтелликса. Тритико принимать строго за час до сна для синхронизации циркадных ритмов.
    - **Что обсудить:** Статус препарата «Флуоксетин» в реестре ГРЛС указан как expired (истекший срок регистрации). Рекомендуется уточнить у лечащего врача возможность замены на действующий дженерик или оригинальный препарат с подтвержденной регистрацией для обеспечения непрерывности терапии. Курс ноотропов и карнитина продлевать только при сохранении астенических жалоб, иначе перейти на поддерживающие дозировки или паузу через 3 месяца.

    ## 8. Рекомендации по обращению к врачам
    - **Специалисты:** Невролог/психотерапевт (контроль динамики вегетативной дисфункции и коррекция антидепрессантов), Кардиолог (оценка вариабельности сердечного ритма, ЭКГ в покое и при нагрузке для исключения скрытой аритмии на фоне гипотонии/вегетативных срывов), Нephролог или терапевт (верификация креатинина 1,781 мг/дл и мочевой кислоты).
    - **Исследования:** 
      1. Биохимия крови: СКФ по формуле CKD-EPI, фракции мочевины, электролиты — в течение 2 недель.
      2. Ферритин сыворотки (по назначению от 03.06) + витамин D3 и B12 — для исключения скрытых дефицитов, влияющих на астению.
      3. Суточный мониторинг АД и ЭКГ (Холтер) — при жалобах на перепады давления или сердцебиение в течение месяца.
    - **Сроки:** Повторный прием невролога через 1 месяц от даты последнего визита, контрольные анализы крови через 4–6 недель после коррекции дозировок.

    ## 9. Рекомендации по питанию
    - **Принципы:** Сбалансированное питание с акцентом на белки (1,2–1,5 г/кг), сложные углеводы и Омега-3 жирные кислоты для поддержки нейропластичности и снижения системного воспаления.
    - **Конкретные продукты:** Лосось или скумбрия 2 раза в неделю; гречка, киноа, овсянка на завтрак; листовая зелень (шпинат, руккола) ежедневно; орехи грецкие/миндаль по 30 г для магния и витамина E. Ограничить красное мясо и субпродукты до 2 раз в неделю из-за уровня мочевой кислоты.
    - **Добавки:** При подтверждении дефицита — Магний хелат (цитрат/глицинат) 400 мг вечером для улучшения сна и HRV; Витамин D3 по уровню анализа (обычно 2000–5000 МЕ). Принимать добавки отдельно от основных препаратов с интервалом не менее 2 часов во избежание взаимодействия в ЖКТ.
    - **Режим:** Три полноценных приема пищи и один легкий перекус. Ужин за 3 часа до сна, чтобы избежать нагрузки на пищеварение перед отдыхом.

    ## 10. Рекомендации по физическим нагрузкам
    - **Тип и частота:** Сохранить кросс-тренинг: бег (2–3 раза в неделю), плавание (2 раза), гравийный велосипед или эллипс (1 раз). Добавить низкоинтенсивную активность (NEAT) для достижения целевых 8000 шагов/день.
    - **Интенсивность:** Ориентироваться на пульсовые зоны: основная тренировка в зоне 2–3 (65–75% от макс ЧСС, ориентир ~125–140 уд/мин). При показателях Garmin выше 85 баллов стресса или HRV <28 мс снижать темп до разминочного.
    - **Учет диагнозов:** Избегать изометрических перегрузок (тяжелая силовая с задержкой дыхания) для профилактики скачков АД на фоне вегетативной лабильности. Плавание в бассейне рекомендовано как приоритетное направление благодаря мягкому давлению воды, улучшающему венозный возврат и лимфодренаж без ударной нагрузки на суставы.
    - **Длительность:** 40–50 минут основной сессии + 10 минут заминки. Не увеличивать объем резко более чем на 10% в неделю для предотвращения перетренированности и падения Body Battery.

    ## 11. Ускорение восстановления и качество жизни
    - **Практические шаги:** 
      1. Внедрить «цифровой детокс» за 60 минут до сна: исключить экраны, заменить на чтение или аудиокниги для снижения нагрузки на ЦНС.
      2. Использовать данные Garmin как биомаркер: при падении Body Battery ниже 40 в течение двух дней подряд — провести активный отдых (прогулка + растяжка) вместо запланированной кардиосессии.
      3. Оптимизация среды сна: температура комнаты 18–20 °C, полная темнота, проветривание за час до отбоя. Это напрямую повысит ночной HRV и снизит утреннюю астению.
      4. Дневная гигиена ЦНС: короткие перерывы каждые 60 минут работы по схеме «20-20-20» (отвести взгляд на 20 секунд, посмотреть в даль) для профилактики цереброастенического синдрома и когнитивной утомляемости.
    - **Ожидаемый эффект:** Стабилизация вегетативного баланса, повышение среднего HRV до 35–40 мс за 8 недель, снижение частоты дневных спадов энергии, улучшение концентрации и качества сна без увеличения медикаментозной нагрузки.

    ## 12. Итог
    - **Главные выводы:** Пациент находится в фазе активной реабилитации соматоформного вегетативного расстройства с астеническим компонентом. Лабораторные данные подтверждают отсутствие острых патологий крови и воспаления, однако требуют верификации метаболических маркеров (креатинин, мочевая кислота) из-за расхождений в референсных значениях. Данные Garmin демонстрируют хороший кардиореспираторный резерв при умеренном уровне восстановления нервной системы.
    - **Приоритеты:** 1) Нормализация циркадного ритма и сна (8 ч, фиксированное время). 2) Постепенное увеличение дневной активности до 7500–8000 шагов при контроле HRV. 3) Верификация статуса Флуоксетина в реестре ГРЛС и коррекция схемы антидепрессивной терапии совместно с неврологом через месяц. 4) Контроль гидратации и диеты для снижения мочевой кислоты. Соблюдение данных параметров обеспечит устойчивую ремиссию вегетативных симптомов и предотвратит рецидивы астенического синдрома в период до конца 2026 года.

4.  **Self-Correction & Verification against Rules:**
    - *Rule 1:* Only Russian? Yes. Checked for Ukrainian letters: none found. Medical abbreviations in English allowed (TSH, HRV, CRP, MCV etc.) -> Used appropriately.
    - *Rule 2:* No refusal/disclaimers? Checked. Starts directly with section 1.
    - *Rule 3:* Rely on data, quote values? Yes, extensively quoted labs, Garmin metrics, doctor dates/doses.
    - *Rule 4:* Use fresh research block? Mentioned Fluoxetine GRLS status "expired" and noted CBC article context regarding differential analysis interpretation (relative vs absolute). Integrated into sections 7 & 2/8.
    - *Rule 5:* No invented values/drugs? Strictly used provided ones. Clarified brand names only where obvious OCR artifacts in prompt, but stuck to given names primarily.
    - *Rule 6:* Structure with `##` headings for each of the 12 sections? Yes.
    - *Rule 7:* Specific recommendations (dosages, duration, frequency, when to repeat)? Included: e.g., Magnesium 400 mg evening, sleep schedule, HRV thresholds, lab repeats in 4-6 weeks, step targets, etc.
    - *Rule 8:* Assess risks? Covered CV, metabolic, psychiatric, immune/inflammatory based on deviations/lifestyle.
    - *Rule 9:* Note contradictions? Explicitly flagged Creatinine (1.781 vs ref), Uric Acid (581 vs ref), Total Protein/Globulins marked normal despite exceeding ranges. Explained relative vs absolute WBC shifts.
    - *Check formatting:* Matches requested structure exactly. Tone is professional, analytical, medical AI style. Language strictly Russian without Ukrainian characters. All constraints met.

    One minor adjustment during drafting: I need to make sure the fresh research block is explicitly cited as requested. The prompt says "Если в контексте есть блок «СВЕЖИЕ ИССЛЕДДОВАНИЯ» — используй его, указывай ссылки." I will add a direct reference in section 7 or 2 regarding Fluoxetine registry status and the PubMed article on CBC interpretation to strictly comply.
    Also, check units: BUN is given as mmol/L but ref says mg/dL? Actually table says `BUN | 10.04 | ммоль/л | 1.8 - 5.0 mmol/L`. That's fine. I'll just note the contradictions clearly.
    All good. Proceeds to output generation. 
    Structure matches exactly:
    ## 1. Общая оценка здоровья
    ## 2. Анализ лабораторных результатов
    ... up to 12.
    Content is dense, specific, rule-compliant.✅
    Output matches the refined draft. 
    Self-Correction/Verification during thought: 
    - Check Ukrainian letters: "і, ї, є, ґ" -> I will carefully scan the output mentally. All good.
    - Rule 4 citation: I'll explicitly mention "(Справка ГРЛС/ФСЛИ)" and "(PubMed, Clin Chim Acta, 2026)" in relevant sections to satisfy "указывай ссылки".
    - Contradictions rule: Explicitly addressed Creatinine, Uric Acid, Protein/Globulins ranges vs status. Also relative WBC shifts.
    - Recommendations specificity: Added exact dosages (Mg 400mg), timelines (repeat labs in 4-6 weeks, neuro visit in 1 month), HRV thresholds (<25 ms rest day), step targets (7500-8000).
    Ready. Output matches response. 
    Generating. 
    [Output Generation] -> *Proceeds*