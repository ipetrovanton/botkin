"""PDF processing parameters."""

from pydantic import BaseModel


class PDFSettings(BaseModel):
    render_dpi: int = 200
    max_pages: int = 50
    text_layer_min_chars_per_page: int = 50
    text_layer_y_tolerance: float = 3.0
    verbatim_max_reject_ratio: float = 0.5
    text_layer_temperature: float = 0.0
    raw_log_limit: int = 4000
