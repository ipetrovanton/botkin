"""Image preprocessing parameters."""

from pydantic import BaseModel


class ImageSettings(BaseModel):
    extract_long_side: int = 2200
    jpeg_quality: int = 90
    classify_long_side: int = 1000
    clahe_clip: float = 2.0
    clahe_tile: int = 8
    unsharp_amount: float = 1.5
    unsharp_sigma: float = 3.0
    deskew_min_angle: float = 3.0
    deskew_min_area: float = 0.40
    deskew_max_area: float = 0.97
    deskew_open_kernel: int = 9
    deskew_close_kernel: int = 35
    lowres_warn: int = 1500
