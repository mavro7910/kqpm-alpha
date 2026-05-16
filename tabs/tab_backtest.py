"""tabs/tab_backtest.py — 백테스트 탭"""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from datetime import date, timedelta

from core.portfolio import Portfolio
from core.strategy import run_backtest, calc_xirr_from_backtest, BENCHMARKS
from utils.ui import section_title, banner, metric_card, badge, TEAL, TEAL_DARK, TEAL_LIGHT, TEXT, TEXT_SUB, TEXT_MUTED, BORDER, SURFACE
from utils.plotly_theme import base_layout, TEAL, BLUE, AMBER, RED, PURPLE, GREEN, TICK_COLOR, FONT_COLOR


def _calc_sharpe(series, risk_free=0.04):
    if len(series) < 4: return float("nan")
    wr = series.pct_change().dropna()
    if wr.std() == 0 or len(wr) < 4: return float("nan")
    return (wr.mean() * 52 - risk_free) / (wr.std() * np.sqrt(52))


def _calc_annual_vol(series):
    wr = series.pct_change().dropna()
    if len(wr) < 4: return float("nan")
    return float(wr.std() * np.sqrt(52) * 100)


def render(portfolio: Portfolio):
    mcap_preset = portfolio.get_setting("mcap_preset", "balanced")
    n_tickers   = portfolio.get_setting("top_n", 10)
    buy_res     = st.session_state.get("buy_result")

    _PRESET_LABELS = {"factor": "순수 팩터", "balanced": "균형", "mcap": "시총 편향"}
    mcap_label = _PRESET_LABELS.get(mcap_preset, mcap_preset)

    if buy_res:
        tickers_r    = buy_res["tickers"]
        weights      = buy_res["weights"]
        weight_parts = ", ".join(f"{t} {weights.get(t,0)*100:.1f}%" for t in tickers_r)
        with st.expander("📌 매수 추천 탭 설정 반영 중", expanded=False):
            st.markdown(banner(
                f"<b>시총 반영:</b> {mcap_label} · <b>Top N:</b> {n_tickers}<br>"
                f"<b>종목 비중:</b> {weight_parts}", "info"
            ), unsafe_allow_html=True)

    with st.expander("ℹ️ 백테스트 해석 주의사항", expanded=False):
        st.markdown("""
<div class="info-banner">
① <b>생존 편향</b>: 현재 보유 종목으로만 시뮬레이션하므로 성과가 과대평가될 수 있습니다.<br>
② <b>시가총액 근사</b>: 과거 시총은 현재 발행주수 × 과거 주가로 근사합니다.<br>
③ <b>수익률 지표</b>: CAGR 대신 <b>XIRR</b>(적립식 내부수익률)을 사용합니다.<br>
④ <b>t=0 통일</b>: KQPM Alpha 전략과 벤치마크를 동일 시점·동일 금액으로 매수하여 공정 비교합니다.
</div>
""", unsafe_allow_html=True)

    _period_opts  = ["1년", "2년", "3년", "5년", "직접 입력"]
    _saved_period = portfolio.get_setting("bt_period", "3년")
    _period_idx   = _period_opts.index(_saved_period) if _saved_period in _period_opts else 2

    col_p, col_bm, col_run2 = st.columns([1.5, 3, 1.5])
    with col_p:
        period_label = st.selectbox("기간", _period_opts, index=_period_idx)
        period_map   = {"1년": "1y", "2년": "2y", "3년": "3y", "5년": "5y"}
        period_str   = period_map.get(period_label, "3y")
    with col_bm:
        bm_input = st.text_input("벤치마크 티커 (쉼표 구분)", value=", ".join(portfolio.benchmarks))
    with col_run2:
        st.markdown('<div style="height:1.6rem"></div>', unsafe_allow_html=True)
        run_bt = st.button("▶ 백테스트 실행", key="btn_bt", type="primary", width="stretch")

    custom_start = custom_end = None
    if period_label == "직접 입력":
        col_sd, col_ed = st.columns(2)
        with col_sd:
            custom_start = st.date_input("시작일", value=date.today() - timedelta(days=1095),
                                         max_value=date.today() - timedelta(days=1), key="bt_custom_start")
        with col_ed:
            custom_end = st.date_input("종료일", value=date.today(),
                                       max_value=date.today(), key="bt_custom_end")
        if custom_start >= custom_end:
            st.error("시작일이 종료일보다 앞서야 합니다.")

    if period_label == "1년":
        st.markdown(
            '<div class="info-banner">ℹ️ 1년 선택 시 워밍업(252거래일) 확보를 위해 내부적으로 약 2년치 데이터를 수집합니다.</div>',
            unsafe_allow_html=True,
        )

    st.caption(
        f"📌 주간 투자금: ₩{portfolio.weekly_budget:,} · 종목 {len(portfolio.holdings)}개 · "
        f"Top {n_tickers} · {mcap_label}"
    )

    if run_bt:
        if not portfolio.tickers():
            st.error("포트폴리오 탭에서 종목을 먼저 추가하세요.")
        elif period_label == "직접 입력" and (
            custom_start is None or custom_end is None or custom_start >= custom_end
        ):
            st.error("올바른 날짜 범위를 입력하세요.")
        else:
            bm_tickers = [b.strip().upper() for b in bm_input.split(",") if b.strip()]
            portfolio.benchmarks = bm_tickers
            portfolio.set_setting("bt_period", period_label)
            portfolio.save()

            if period_label == "직접 입력":
                sim_start_date = custom_start.strftime("%Y-%m-%d")
                end_date       = custom_end.strftime("%Y-%m-%d")
            else:
                period_to_days = {"1y": 365, "2y": 730, "3y": 1095, "5y": 1825}
                end_date       = date.today().strftime("%Y-%m-%d")
                sim_start_date = (date.today() - timedelta(days=period_to_days[period_str])).strftime("%Y-%m-%d")

            progress_bar = st.progress(0)
            status_text  = st.empty()

            def progress_cb(cur, total):
                pct = int(cur / total * 100) if total > 0 else 0
                progress_bar.progress(pct)
                status_text.caption(f"백테스트 진행 중... ({cur}/{total} 주)")

            try:
                with st.spinner("데이터 수집 중..."):
                    df_bt = run_backtest(
                        portfolio.tickers(),
                        weekly_budget=portfolio.weekly_budget,
                        benchmark_tickers=bm_tickers,
                        period=period_str,
                        mcap_preset=mcap_preset,
                        progress_cb=progress_cb,
                        top_n=int(n_tickers),
                        start=None, end=end_date,
                        sim_start=sim_start_date,
                    )
                st.session_state["bt_result"] = df_bt
                progress_bar.progress(100)
                status_text.caption("✅ 완료!")
            except Exception as e:
                st.error(f"백테스트 오류: {e}")
                import traceback
                st.code(traceback.format_exc())
                progress_bar.empty()
                status_text.empty()

    if "bt_result" not in st.session_state:
        return

    df_bt   = st.session_state["bt_result"]
    bm_cols = [c for c in df_bt.columns if c not in ("QPM_Alpha", "Invested")]

    chart_mode = st.radio(
        "차트 기준", ["평가금액 (KRW)", "누적 수익률 (%)"],
        horizontal=True, key="bt_chart_mode",
    )

    # ── 차트 ──────────────────────────────────────────────
    bm_colors = [BLUE, AMBER, RED, PURPLE, GREEN]
    fig = go.Figure()
    lay = base_layout("", height=500)

    if chart_mode == "평가금액 (KRW)":
        fig.add_trace(go.Scatter(
            x=df_bt.index, y=df_bt["Invested"], name="누적 투자금",
            fill="tozeroy", fillcolor="rgba(15,110,86,0.05)",
            line=dict(color="rgba(15,110,86,0.3)", width=1, dash="dot"),
        ))
        fig.add_trace(go.Scatter(
            x=df_bt.index, y=df_bt["QPM_Alpha"], name="KQPM Alpha",
            line=dict(color=TEAL, width=2.5),
            fill="tonexty", fillcolor="rgba(15,110,86,0.06)",
        ))
        for i, col in enumerate(bm_cols):
            fig.add_trace(go.Scatter(
                x=df_bt.index, y=df_bt[col],
                name=BENCHMARKS.get(col, col),
                line=dict(color=bm_colors[i % len(bm_colors)], width=1.8, dash="dash"),
            ))
        lay["yaxis"].update(tickformat=",.0f", tickprefix="₩", exponentformat="none")
        lay["title"]["text"] = "포트폴리오 성과 비교 (평가금액)"
    else:
        invested_s = df_bt["Invested"]
        ret_kh = (df_bt["QPM_Alpha"] / invested_s - 1) * 100
        fig.add_hline(y=0, line_color="rgba(15,110,86,0.25)", line_dash="dot", line_width=1)
        fig.add_trace(go.Scatter(
            x=df_bt.index, y=ret_kh, name="KQPM Alpha",
            line=dict(color=TEAL, width=2.5),
            fill="tozeroy", fillcolor="rgba(15,110,86,0.07)",
        ))
        for i, col in enumerate(bm_cols):
            ret_bm = (df_bt[col] / invested_s - 1) * 100
            fig.add_trace(go.Scatter(
                x=df_bt.index, y=ret_bm,
                name=BENCHMARKS.get(col, col),
                line=dict(color=bm_colors[i % len(bm_colors)], width=1.8, dash="dash"),
            ))
        lay["yaxis"].update(ticksuffix="%", zeroline=True, zerolinecolor="rgba(15,110,86,0.2)")
        lay["title"]["text"] = "포트폴리오 성과 비교 (누적 수익률)"

    lay.update(dict(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(255,255,255,0.6)",
        xaxis=dict(
            gridcolor="rgba(15,110,86,0.06)", showgrid=False,
            tickfont=dict(color=TICK_COLOR, size=10), tickangle=-25,
        ),
        yaxis=dict(gridcolor="rgba(15,110,86,0.08)", tickfont=dict(color=TICK_COLOR, size=10)),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.04, xanchor="right", x=1,
            bgcolor="rgba(0,0,0,0)",
            bordercolor="rgba(0,0,0,0)", borderwidth=0,
            font=dict(color=FONT_COLOR, size=11),
        ),
        margin=dict(t=82, b=48, l=60, r=16),
    ))
    fig.update_layout(lay)
    st.plotly_chart(fig, width="stretch", key="bt_chart")

    # ── 성과 요약 ──────────────────────────────────────────
    st.markdown(section_title("성과 요약"), unsafe_allow_html=True)

    invested_final = df_bt["Invested"].iloc[-1]
    summary_rows   = []

    for col in ["QPM_Alpha"] + bm_cols:
        final   = df_bt[col].iloc[-1]
        ret_pct = (final / invested_final - 1) * 100 if invested_final > 0 else 0.0
        xirr_val = calc_xirr_from_backtest(df_bt, portfolio.weekly_budget, col=col)
        xirr_pct = xirr_val * 100 if not np.isnan(xirr_val) else None
        running_max = df_bt[col].cummax()
        mdd = ((running_max - df_bt[col]) / running_max.replace(0, np.nan)).max() * 100
        sharpe  = _calc_sharpe(df_bt[col])
        ann_vol = _calc_annual_vol(df_bt[col])

        summary_rows.append({
            "전략":              "KQPM Alpha" if col == "QPM_Alpha" else BENCHMARKS.get(col, col),
            "최종 평가금액 (M원)": final / 1_000_000,
            "누적 수익률 (%)":   ret_pct,
            "XIRR (%)":         xirr_pct,
            "MDD (%)":          mdd,
            "연 변동성 (%)":     ann_vol,
            "Sharpe":           sharpe if not np.isnan(sharpe) else None,
        })

    summary_rows.append({
        "전략":              "📌 누적 투자금",
        "최종 평가금액 (M원)": invested_final / 1_000_000,
        "누적 수익률 (%)":   None,
        "XIRR (%)":         None,
        "MDD (%)":          None,
        "연 변동성 (%)":     None,
        "Sharpe":           None,
    })

    st.dataframe(
        pd.DataFrame(summary_rows),
        column_config={
            "최종 평가금액 (M원)": st.column_config.NumberColumn(format="₩%.2fM"),
            "누적 수익률 (%)":     st.column_config.NumberColumn(format="%+.1f%%"),
            "XIRR (%)":           st.column_config.NumberColumn(format="%+.1f%%"),
            "MDD (%)":            st.column_config.NumberColumn(format="%.1f%%"),
            "연 변동성 (%)":       st.column_config.NumberColumn(format="%.1f%%"),
            "Sharpe":             st.column_config.NumberColumn(format="%.2f"),
        },
        width="stretch", hide_index=True,
    )
    st.caption(
        f"기간: {df_bt.index[0].date()} ~ {df_bt.index[-1].date()} ({len(df_bt)}주) · "
        "XIRR = 적립식 내부수익률 · Sharpe = 무위험수익률 4% 기준"
    )
