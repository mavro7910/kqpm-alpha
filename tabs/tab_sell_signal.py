"""tabs/tab_sell_signal.py — 매도 신호 탭"""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.portfolio import Portfolio
from core.strategy import (
    fetch_prices, fetch_market_caps,
    MOMENTUM_WEIGHTS, MCAP_PRESETS, VOL_WINDOW, MA_WINDOW,
)
from core.data import MARKET_BENCHMARK
from utils.ui import section_title, banner, metric_card, badge, TEAL, TEAL_DARK, TEAL_LIGHT, TEXT, TEXT_SUB, TEXT_MUTED, BORDER, SURFACE
from utils.plotly_theme import TEAL, FONT_COLOR, TICK_COLOR

_PRESET_LABELS = {
    "factor":   "순수 팩터",
    "balanced": "균형",
    "mcap":     "시총 편향",
}


def render(portfolio: Portfolio):
    with st.expander("📌 매도 신호란?", expanded=False):
        st.markdown(banner(
            "지난 1달(약 21 거래일) 동안 <b>매일</b> 종목별 랭킹을 계산하여 "
            "한 번도 설정한 순위(Top N) 안에 들지 못한 종목을 리스트업합니다.<br>"
            "· <b>매도 후보</b>: 21일간 단 하루도 Top N 미진입<br>"
            "· <b>관찰 종목</b>: 진입률 50% 미만", "info"
        ), unsafe_allow_html=True)

    col_top, col_mcap6, col_run6 = st.columns([2, 2, 1.2])
    with col_top:
        _ticker_count   = len(portfolio.tickers())
        _max_sell       = max(_ticker_count, 1) if _ticker_count > 0 else 50
        _saved_sell_n   = portfolio.get_setting("sell_top_n", 15)
        _default_sell_n = max(1, min(_saved_sell_n, _max_sell))
        top_n_sell = st.number_input(
            "유지 기준 순위 (Top N)",
            min_value=1, max_value=_max_sell, value=_default_sell_n, step=1,
            help=f"현재 유니버스 종목 수: {_ticker_count}개",
            disabled=(_ticker_count == 0),
        )
    with col_mcap6:
        _saved_preset = portfolio.get_setting("sell_mcap_preset", "balanced")
        _preset_opts  = list(_PRESET_LABELS.keys())
        _preset_idx   = _preset_opts.index(_saved_preset) if _saved_preset in _preset_opts else 1
        mcap_preset = st.radio(
            "시총 반영",
            options=_preset_opts,
            format_func=lambda k: _PRESET_LABELS[k],
            index=_preset_idx,
            horizontal=False,
            key="sell_mcap_preset",
        )
    with col_run6:
        st.markdown('<div style="height:1.6rem"></div>', unsafe_allow_html=True)
        run_sell = st.button("🔍 분석 실행", key="btn_sell_signal", type="primary")

    if run_sell:
        if not portfolio.tickers():
            st.error("포트폴리오 탭에서 종목을 먼저 입력하세요.")
        else:
            portfolio.set_setting("sell_top_n", int(top_n_sell))
            portfolio.set_setting("sell_mcap_preset", mcap_preset)
            portfolio.save()
            _run_sell_analysis(portfolio, top_n_sell, mcap_preset)

    if "sell_result" in st.session_state:
        _render_sell_result(portfolio, top_n_sell)


def _run_sell_analysis(portfolio, top_n_sell, mcap_preset: str):
    tickers_all = portfolio.tickers()
    for _k in ("mcap_cache6", "mcap_cache6_ok"):
        st.session_state.pop(_k, None)

    gamma = MCAP_PRESETS.get(mcap_preset, MCAP_PRESETS["balanced"])

    with st.spinner("1달치 일별 랭킹 계산 중..."):
        try:
            mcap_series = None
            mcap_ok     = False
            if gamma > 0:
                mcap_series, mcap_ok = fetch_market_caps(tickers_all)
                st.session_state["mcap_cache6"]    = mcap_series
                st.session_state["mcap_cache6_ok"] = mcap_ok
                if not mcap_ok:
                    gamma = 0.0

            data6   = fetch_prices(tickers_all, extra=[MARKET_BENCHMARK], period="14mo")
            prices6 = data6["prices"].reindex(columns=tickers_all).ffill()
            qqq6    = data6.get(MARKET_BENCHMARK, pd.Series(dtype=float))

            trading_days = prices6.index[-21:]
            daily_ranks  = {}

            mcap_z_full = pd.Series(0.0, index=prices6.columns)
            if gamma > 0 and mcap_series is not None:
                mc = mcap_series.reindex(prices6.columns).fillna(0)
                if mc.sum() > 1e-8:
                    sig = float(mc.std())
                    if sig > 1e-8:
                        mcap_z_full = ((mc - mc.mean()) / sig).clip(-3, 3)

            for date in trading_days:
                loc       = prices6.index.get_loc(date)
                start_loc = max(0, loc - 252)
                sub       = prices6.iloc[start_loc: loc + 1]
                if len(sub) < 22:
                    continue

                mom     = pd.Series(0.0, index=sub.columns)
                total_w = 0.0
                for days, w in MOMENTUM_WEIGHTS.items():
                    if len(sub) <= days: continue
                    ret = sub.pct_change(days).iloc[-1].fillna(0)
                    mom += w * ret.rank(pct=True)
                    total_w += w
                if total_w > 0: mom /= total_w

                vol      = sub.pct_change().rolling(VOL_WINDOW).std().iloc[-1]
                inv      = (1 / vol.replace(0, np.nan)).fillna(0)
                vol_rank = inv.rank(pct=True) if inv.sum() > 0 else pd.Series(1.0 / len(sub.columns), index=sub.columns)

                if qqq6 is not None and len(qqq6) > loc:
                    qqq_sub  = qqq6.iloc[start_loc: loc + 1]
                    is_bull6 = float(qqq_sub.iloc[-1]) > float(qqq_sub.rolling(MA_WINDOW).mean().iloc[-1]) if len(qqq_sub) >= MA_WINDOW else True
                else:
                    is_bull6 = True

                m_w6 = 0.7 if is_bull6 else 0.4
                v_w6 = 0.3 if is_bull6 else 0.6
                alpha = (mom ** (2.0 if is_bull6 else 1.2)) * m_w6 + (vol_rank ** 1.5) * v_w6

                if gamma > 0:
                    mcap_z      = mcap_z_full.reindex(sub.columns).fillna(0)
                    mcap_z_norm = mcap_z - mcap_z.min()
                    _denom      = mcap_z_norm.max()
                    if _denom > 1e-8: mcap_z_norm = mcap_z_norm / _denom
                    alpha = alpha * (1.0 - gamma) + mcap_z_norm * gamma

                combined    = alpha / alpha.sum() if alpha.sum() > 0 else alpha
                daily_ranks[date] = combined.rank(ascending=False, method="min").astype(int)

            if not daily_ranks:
                st.error("랭킹을 계산할 수 있는 거래일이 부족합니다.")
                return

            rank_df     = pd.DataFrame(daily_ranks).T
            total_days  = len(rank_df)
            in_top_n    = (rank_df <= top_n_sell).sum()
            best_rank   = rank_df.min()
            avg_rank    = rank_df.mean()
            latest_rank = rank_df.iloc[-1]

            st.session_state["sell_result"] = {
                "top_n": top_n_sell, "mcap_preset": mcap_preset,
                "mcap_gamma": gamma, "mcap_ok": mcap_ok,
                "total_days": total_days, "in_top_n": in_top_n,
                "best_rank": best_rank, "avg_rank": avg_rank,
                "latest_rank": latest_rank, "rank_df": rank_df,
            }
        except Exception as e:
            st.error(f"오류 발생: {e}")
            import traceback
            st.code(traceback.format_exc())


def _render_sell_result(portfolio, top_n_sell):
    sr          = st.session_state["sell_result"]
    top_n_v     = sr["top_n"]
    mcap_preset = sr.get("mcap_preset", "balanced")
    mcap_ok     = sr.get("mcap_ok", True)
    total_days  = sr["total_days"]
    in_top_n    = sr["in_top_n"]
    best_rank   = sr["best_rank"]
    avg_rank    = sr["avg_rank"]
    latest_rank = sr["latest_rank"]
    rank_df     = sr["rank_df"]
    tickers_all = portfolio.tickers()

    if mcap_preset != "factor" and not mcap_ok:
        st.markdown(
            '<div class="warn-banner">⚠️ 시총 데이터 조회 실패 — 순수 팩터로 자동 전환되었습니다.</div>',
            unsafe_allow_html=True,
        )

    sell_candidates  = in_top_n[in_top_n == 0].index.tolist()
    watch_candidates = in_top_n[(in_top_n > 0) & (in_top_n < total_days * 0.5)].index.tolist()

    st.markdown(f"""
<div class="qpm-sell-summary">
  <div class="qpm-sell-card">
    <div class="qpm-sell-label">분석 기간</div>
    <div class="qpm-sell-value">{total_days}일</div>
  </div>
  <div class="qpm-sell-card">
    <div class="qpm-sell-label">매도 후보</div>
    <div class="qpm-sell-value qpm-sell-danger">{len(sell_candidates)}종목</div>
  </div>
  <div class="qpm-sell-card">
    <div class="qpm-sell-label">관찰 종목</div>
    <div class="qpm-sell-value qpm-sell-warn">{len(watch_candidates)}종목</div>
  </div>
  <div class="qpm-sell-card">
    <div class="qpm-sell-label">시총 반영</div>
    <div class="qpm-sell-value qpm-sell-small">{_PRESET_LABELS.get(mcap_preset, mcap_preset)}</div>
  </div>
</div>
<style>
.qpm-sell-summary {{
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin: 10px 0 22px;
}}
.qpm-sell-card {{
  background: var(--qpm-surface, #F7F8FA);
  border: 0;
  border-radius: 8px;
  padding: 14px 15px;
  min-width: 0;
}}
.qpm-sell-label {{
  font-size: 0.68rem;
  font-weight: 700;
  color: var(--qpm-text-muted, #8A949E);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 6px;
}}
.qpm-sell-value {{
  font-size: 1.25rem;
  font-weight: 700;
  color: var(--qpm-text, #111827);
  line-height: 1.2;
  overflow-wrap: anywhere;
}}
.qpm-sell-small {{ font-size: 1.05rem; }}
.qpm-sell-danger {{ color: #E05252; }}
.qpm-sell-warn {{ color: #C9873A; }}
@media (max-width: 768px) {{
  .qpm-sell-summary {{
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }}
}}
</style>
""", unsafe_allow_html=True)

    if sell_candidates:
        st.markdown(
            f'<div class="danger-banner">🚨 <b>매도 후보 {len(sell_candidates)}종목</b> — '
            f'지난 {total_days}일간 단 하루도 Top {top_n_v} 안에 들지 못했습니다.</div>',
            unsafe_allow_html=True,
        )
        sell_rows = [{
            "로고": portfolio.get_logo(t),
            "티커": t,
            f"Top{top_n_v} 진입 (일)": int(in_top_n[t]),
            "최고 순위": int(best_rank[t]),
            "평균 순위": round(float(avg_rank[t]), 1),
            "최근 순위": int(latest_rank[t]),
            "보유 수량": portfolio.holdings.get(t, 0),
        } for t in sell_candidates]
        st.dataframe(
            pd.DataFrame(sell_rows).sort_values("최근 순위"),
            column_config={
                "로고": st.column_config.ImageColumn(""),
                "보유 수량": st.column_config.NumberColumn(format="%.4f"),
            },
            hide_index=True, width="stretch",
        )
    else:
        st.markdown(
            f'<div class="success-banner">✅ 모든 종목이 지난 {total_days}일 중 최소 1일 이상 Top {top_n_v} 안에 진입했습니다.</div>',
            unsafe_allow_html=True,
        )

    if watch_candidates:
        st.markdown(
            f'<div class="warn-banner">⚠️ <b>관찰 종목 {len(watch_candidates)}종목</b> — 진입률 50% 미만</div>',
            unsafe_allow_html=True,
        )
        watch_rows = [{
            "로고": portfolio.get_logo(t),
            "티커": t,
            f"Top{top_n_v} 진입 (일)": int(in_top_n[t]),
            "진입률 (%)": round(in_top_n[t] / total_days * 100, 1),
            "최고 순위": int(best_rank[t]),
            "평균 순위": round(float(avg_rank[t]), 1),
            "최근 순위": int(latest_rank[t]),
        } for t in watch_candidates]
        st.dataframe(
            pd.DataFrame(watch_rows).sort_values("진입률 (%)"),
            column_config={
                "로고": st.column_config.ImageColumn(""),
                "진입률 (%)": st.column_config.ProgressColumn(min_value=0, max_value=50, format="%.1f%%"),
            },
            hide_index=True, width="stretch",
        )

    # ── 히트맵 ──────────────────────────────────────────
    st.markdown(section_title("일별 순위 히트맵 (최근 1달)"), unsafe_allow_html=True)
    st.caption(f"짙은 파란색에 가까울수록 상위 순위입니다. Top {top_n_v} 밖으로 밀리면 붉은 톤으로 표시됩니다.")
    heatmap_data    = rank_df[tickers_all].T
    date_labels     = [d.strftime("%m/%d") for d in heatmap_data.columns]
    n_tickers_total = len(tickers_all)
    cut = top_n_v / n_tickers_total if n_tickers_total > 0 else 0.5

    colorscale = [
        [0.0,  "#1457A8"],
        [max(cut - 0.001, 0.0), "#EAF2FF"],
        [cut, "#FFF6F2"],
        [1.0,  "#E05252"],
    ]

    fig_hm = go.Figure(go.Heatmap(
        z=heatmap_data.values,
        x=date_labels,
        y=heatmap_data.index.tolist(),
        colorscale=colorscale,
        zmin=1, zmax=n_tickers_total,
        xgap=3,
        ygap=3,
        colorbar=dict(
            title=dict(text="순위", font=dict(size=11, color=FONT_COLOR)),
            tickvals=[1, top_n_v, n_tickers_total],
            ticktext=["1위", f"{top_n_v}위", f"{n_tickers_total}위"],
            tickfont=dict(size=10, color=FONT_COLOR),
            thickness=8, len=0.62,
            outlinewidth=0,
        ),
        text=heatmap_data.values,
        texttemplate="%{text}",
        textfont=dict(size=9, color="#111827"),
        hovertemplate="날짜: %{x}<br>종목: %{y}<br>순위: %{z}위<extra></extra>",
    ))
    fig_hm.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color=FONT_COLOR,
        margin=dict(t=12, b=44, l=70, r=70),
        height=max(280, 26 * n_tickers_total + 70),
        xaxis=dict(
            side="bottom",
            tickangle=-35,
            tickfont=dict(size=9, color=TICK_COLOR),
            showgrid=False,
            zeroline=False,
            showline=False,
        ),
        yaxis=dict(
            tickfont=dict(size=10, color=TICK_COLOR),
            showgrid=False,
            zeroline=False,
            showline=False,
        ),
    )
    st.plotly_chart(fig_hm, width="stretch", key="sell_heatmap")
