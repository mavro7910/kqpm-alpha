"""
utils/plotly_theme.py — KQPM Alpha 공통 Plotly 테마
블루/레드 포인트 기반 차트 스타일
"""

TEAL       = "#1457A8"
BLUE       = "#4a90d9"
AMBER      = "#c9873a"
RED        = "#D94B4B"
PURPLE     = "#8b72c8"
GREEN      = "#5ab87a"
GRAY       = "#a0b4b2"

BG_PAPER   = "rgba(244,251,250,0)"   # transparent — 앱 배경과 자연스럽게 합성
BG_PLOT    = "rgba(255,255,255,0.7)"
GRID_COLOR = "rgba(20,87,168,0.08)"
TICK_COLOR = "#6b8fbd"
FONT_COLOR = "#26364a"
LEGEND_BG  = "rgba(255,255,255,0.85)"

LINE_COLORS = [TEAL, BLUE, AMBER, RED, PURPLE, GREEN]


def base_layout(title: str = "", height: int = 480) -> dict:
    return dict(
        title=dict(
            text=title,
            font=dict(color=FONT_COLOR, size=13, family="sans-serif"),
            x=0,
            pad=dict(l=4),
        ),
        paper_bgcolor=BG_PAPER,
        plot_bgcolor=BG_PLOT,
        font=dict(color=FONT_COLOR, size=11, family="sans-serif"),
        height=height,
        margin=dict(t=44, b=50, l=58, r=16),
        hovermode="x unified",
        hoverlabel=dict(
            bgcolor="rgba(255,255,255,0.95)",
            font_color=FONT_COLOR,
            font_size=11,
            bordercolor=TEAL,
        ),
        xaxis=dict(
            gridcolor=GRID_COLOR,
            tickfont=dict(color=TICK_COLOR, size=10),
            tickangle=-25,
            showgrid=False,
            zeroline=False,
        ),
        yaxis=dict(
            gridcolor=GRID_COLOR,
            tickfont=dict(color=TICK_COLOR, size=10),
            showgrid=True,
            zeroline=False,
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.04,
            xanchor="right", x=1,
            bgcolor="rgba(0,0,0,0)",
            bordercolor="rgba(0,0,0,0)",
            borderwidth=0,
            font=dict(color=FONT_COLOR, size=11),
        ),
    )
