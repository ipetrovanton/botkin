"""PNG-графики через plotly + kaleido."""
from datetime import datetime
from io import BytesIO

import plotly.graph_objects as go


def lab_dynamics_chart(points: list[dict], analyte_name: str) -> bytes:
    """points: [{taken_at, value_num, ref_low, ref_high, unit}, ...] → PNG."""
    if not points:
        return _empty_chart(f"Нет данных по «{analyte_name}»")

    dates = [_parse_date(p["taken_at"]) for p in points]
    values = [p["value_num"] for p in points]
    unit = points[0].get("unit", "")
    ref_low = points[0].get("ref_low")
    ref_high = points[0].get("ref_high")

    fig = go.Figure()
    if ref_low is not None and ref_high is not None:
        fig.add_hrect(
            y0=ref_low, y1=ref_high,
            fillcolor="green", opacity=0.15, line_width=0,
            annotation_text=f"Норма {ref_low}–{ref_high} {unit}",
            annotation_position="top left",
        )
    fig.add_trace(go.Scatter(
        x=dates, y=values, mode="lines+markers",
        line=dict(color="#1976D2", width=3),
        marker=dict(size=10),
        text=[f"{v} {unit}" for v in values],
        hovertemplate="%{x|%Y-%m-%d}<br><b>%{y} " + unit + "</b><extra></extra>",
    ))
    fig.update_layout(
        title=f"Динамика: {analyte_name}",
        xaxis_title="Дата",
        yaxis_title=f"Значение, {unit}",
        template="plotly_white",
        width=900, height=500,
    )
    return _to_png(fig)


def _parse_date(s):
    if isinstance(s, datetime):
        return s
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _to_png(fig) -> bytes:
    buf = BytesIO()
    fig.write_image(buf, format="png", scale=2)
    return buf.getvalue()


def _empty_chart(text: str) -> bytes:
    fig = go.Figure()
    fig.add_annotation(text=text, showarrow=False, font=dict(size=20))
    fig.update_layout(
        template="plotly_white", width=600, height=300,
        xaxis=dict(visible=False), yaxis=dict(visible=False),
    )
    return _to_png(fig)