import sqlite3
import json
import sys
from pathlib import Path

# Добавляем родительскую директорию в path
sys.path.insert(0, str(Path(__file__).parent.parent))

from parsing.drug_instruction_extractor import _parse_with_regex, _parse_with_llm, _validate_parse_quality, _is_junk_source, ParsedInstruction

def run_parser():
    """Запускает парсер и перезаписывает таблицу."""
    conn = sqlite3.connect('data/botkin.db')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute('DROP TABLE IF EXISTS drug_parsed_instructions')
    cur.execute('''
        CREATE TABLE drug_parsed_instructions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL UNIQUE,
            reg_number TEXT,
            trade_name TEXT,
            mnn TEXT,
            synonyms TEXT,
            dosage_form TEXT,
            release_form_and_packaging TEXT,
            packaging TEXT,
            manufacturer TEXT,
            atx_code TEXT,
            pharmacological_group TEXT,
            pharmacological_properties TEXT,
            pharmacological_action TEXT,
            pharmacodynamics TEXT,
            mechanism_of_action TEXT,
            pharmacokinetics TEXT,
            composition TEXT,
            excipients TEXT,
            description TEXT,
            indications TEXT,
            contraindications TEXT,
            contraindications_absolute TEXT,
            use_with_caution TEXT,
            dosage_and_administration TEXT,
            side_effects TEXT,
            interactions TEXT,
            overdose TEXT,
            overdose_symptoms TEXT,
            overdose_treatment TEXT,
            special_instructions TEXT,
            notes TEXT,
            driving_ability TEXT,
            pregnancy_and_lactation TEXT,
            pregnancy_category TEXT,
            pediatric_use TEXT,
            geriatric_use TEXT,
            renal_impairment TEXT,
            hepatic_impairment TEXT,
            storage_conditions TEXT,
            shelf_life TEXT,
            dispensing_conditions TEXT,
            coating_composition TEXT,
            analogs TEXT,
            parse_method TEXT DEFAULT 'regex',
            filled_fields_count INTEGER DEFAULT 0,
            extra_fields_json TEXT,
            FOREIGN KEY (source_id) REFERENCES drug_instructions(id)
        )
    ''')

    cur.execute('SELECT id, trade_name, mnn, reg_number, raw_text FROM drug_instructions WHERE raw_text IS NOT NULL')
    rows = cur.fetchall()

    total = len(rows)
    good = 0
    total_fields = 0
    errors = 0

    for i, row in enumerate(rows):
        try:
            result = _parse_with_regex(row['raw_text'], row['id'], row['trade_name'], row['mnn'], row['reg_number'])
            if result.filled_fields_count < 4 or _validate_parse_quality(result):
                result = _parse_with_llm(row['raw_text'], row['id'], row['trade_name'], row['mnn'], row['reg_number'])

            if result.filled_fields_count >= 4:
                good += 1
            total_fields += result.filled_fields_count

            # Используем словарь для вставки
            data = {
                'source_id': result.source_id,
                'reg_number': result.reg_number,
                'trade_name': result.trade_name,
                'mnn': result.mnn,
                'synonyms': result.synonyms,
                'dosage_form': result.dosage_form,
                'release_form_and_packaging': result.release_form_and_packaging,
                'packaging': result.packaging,
                'manufacturer': result.manufacturer,
                'atx_code': result.atx_code,
                'pharmacological_group': result.pharmacological_group,
                'pharmacological_properties': result.pharmacological_properties,
                'pharmacological_action': result.pharmacological_action,
                'pharmacodynamics': result.pharmacodynamics,
                'mechanism_of_action': result.mechanism_of_action,
                'pharmacokinetics': result.pharmacokinetics,
                'composition': result.composition,
                'excipients': result.excipients,
                'description': result.description,
                'indications': result.indications,
                'contraindications': result.contraindications,
                'contraindications_absolute': result.contraindications_absolute,
                'use_with_caution': result.use_with_caution,
                'dosage_and_administration': result.dosage_and_administration,
                'side_effects': result.side_effects,
                'interactions': result.interactions,
                'overdose': result.overdose,
                'overdose_symptoms': result.overdose_symptoms,
                'overdose_treatment': result.overdose_treatment,
                'special_instructions': result.special_instructions,
                'notes': result.notes,
                'driving_ability': result.driving_ability,
                'pregnancy_and_lactation': result.pregnancy_and_lactation,
                'pregnancy_category': result.pregnancy_category,
                'pediatric_use': result.pediatric_use,
                'geriatric_use': result.geriatric_use,
                'renal_impairment': result.renal_impairment,
                'hepatic_impairment': result.hepatic_impairment,
                'storage_conditions': result.storage_conditions,
                'shelf_life': result.shelf_life,
                'dispensing_conditions': result.dispensing_conditions,
                'coating_composition': result.coating_composition,
                'analogs': result.analogs,
                'parse_method': result.parse_method,
                'filled_fields_count': result.filled_fields_count,
                'extra_fields_json': result.extra_fields_json,
            }

            columns = ', '.join(data.keys())
            placeholders = ', '.join(['?'] * len(data))
            cur.execute(f'INSERT INTO drug_parsed_instructions ({columns}) VALUES ({placeholders})', list(data.values()))

            if (i + 1) % 500 == 0:
                conn.commit()
        except Exception as e:
            errors += 1

    conn.commit()
    conn.close()
    return total, good, total_fields, errors


def check_first_100():
    """Проверяет первые 100 записей и сохраняет для анализа."""
    conn = sqlite3.connect('data/botkin.db')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute('''
        SELECT source_id, reg_number, trade_name, mnn, dosage_form,
               pharmacological_group, indications, contraindications
        FROM drug_parsed_instructions
        ORDER BY source_id
        LIMIT 100
    ''')

    results = []
    for row in cur.fetchall():
        results.append(dict(row))

    with open('parsing/first_100_check.json', 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    conn.close()
    return results


def check_raw_text(source_id):
    """Смотрит исходный raw_text для анализа проблем."""
    conn = sqlite3.connect('data/botkin.db')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute('SELECT raw_text FROM drug_instructions WHERE id = ?', (source_id,))
    row = cur.fetchone()

    conn.close()
    return row['raw_text'] if row else None


if __name__ == '__main__':
    print('Запуск парсера...')
    total, good, total_fields, errors = run_parser()
    print(f'Итого: {total} записей, Good: {good} ({100*good/total:.1f}%), Avg fields: {total_fields/total:.1f}, Errors: {errors}')

    print('\nПроверка первых 100 записей...')
    results = check_first_100()
    print(f'Сохранено в parsing/first_100_check.json')

    # Проверим на явные проблемы
    issues = []
    for r in results:
        # Проверяем на мусор в ключевых полях
        if r['trade_name'] and len(r['trade_name']) < 3:
            issues.append(f"id={r['source_id']}: trade_name слишком короткий: {r['trade_name']!r}")
        if r['mnn'] and len(r['mnn']) < 3:
            issues.append(f"id={r['source_id']}: mnn слишком короткий: {r['mnn']!r}")
        if r['trade_name'] and r['trade_name'].isdigit():
            issues.append(f"id={r['source_id']}: trade_name только цифры: {r['trade_name']!r}")
        if r['mnn'] and 'Форма выпуска' in r['mnn']:
            issues.append(f"id={r['source_id']}: mnn содержит заголовок: {r['mnn']!r}")

    if issues:
        print(f'\nНайдено проблем: {len(issues)}')
        for issue in issues[:10]:
            print(f"  {issue}")
    else:
        print('\nЯвных проблем не обнаружено в первых 100 записях')