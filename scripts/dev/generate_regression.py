import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path("botkin-core").absolute()))

from parsing.ocr.router import run as ocr_run
from parsing.llm.extract import run_analysis
from parsing.ocr.preprocess import has_text_layer
from concurrent.futures import ThreadPoolExecutor

raw_dir = Path(r"C:\Sandbox\botkin\test-dataset\datasets\medknow-test\raw\user_samples")
output_file = Path("test_data/regression_analysis.jsonl")

def process_file(pdf_path):
    try:
        with open("generate_log.txt", "a", encoding="utf-8") as log_f:
            log_f.write(f"Processing {pdf_path.name}...\n")
            log_f.flush()
    except Exception:
        pass

    try:
        if has_text_layer(pdf_path):
            ocr_result = None
        else:
            ocr_result = ocr_run(pdf_path)
            
        lab_results = run_analysis(ocr_result, source_path=pdf_path)
        expected = []
        for r in lab_results:
            if r.value_num is not None:
                expected.append({
                    "analyte_name": r.analyte_name,
                    "value_num": r.value_num,
                    "unit": r.unit
                })
        return {"file": str(pdf_path), "expected": expected}
    except Exception as e:
        try:
            with open("generate_log.txt", "a", encoding="utf-8") as log_f:
                log_f.write(f"Failed {pdf_path.name}: {e}\n")
                log_f.flush()
        except Exception:
            pass
        return None

# Находим файлы и сортируем их: сначала все Digital, в конце Scanned.
# Это позволяет сделать ровно ОДНУ замену моделей во VRAM Ollama (Text -> VLM)
# и экономит нам более 8-10 минут времени!
files = list(raw_dir.glob("sample_*.pdf"))[:20]
files.sort(key=lambda f: 0 if has_text_layer(f) else 1)

results = []
with ThreadPoolExecutor(max_workers=1) as ex:
    with open(output_file, "w", encoding="utf-8") as f:
        for res in ex.map(process_file, files):
            if res is not None:
                results.append(res)
                f.write(json.dumps(res, ensure_ascii=False) + "\n")
                f.flush()

print(f"Generated {len(results)} samples.")
