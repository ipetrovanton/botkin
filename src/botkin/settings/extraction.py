"""Extraction pipeline parameters."""

from pydantic import BaseModel


class ExtractionSettings(BaseModel):
    androflor_min_rows: int = 3
    androflor_voting_tries: int = 1
    androflor_retry_long_side: int = 3000
    sibr_min_rows: int = 4
    sibr_voting_tries: int = 1
    text_empty_retries: int = 1
    image_ocr_transient_retries: int = 1


class NormalizeSettings(BaseModel):
    """Fuzzy matching thresholds for drugs and analytes."""
    drug_max_edit_ratio: float = 0.40
    drug_ratio_floor: float = 70.0
    analyte_max_edit_ratio: float = 0.35
    analyte_ratio_floor: float = 75.0
    analyte_short_key_len: int = 3
