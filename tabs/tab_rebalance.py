"""tabs/tab_rebalance.py — 리밸런싱 탭"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.portfolio import Portfolio
from core.strategy import rebalance_weights
from utils.ui import section_title, banner, metric_card, badge, stock_link, TEAL, TEAL_DARK, TEAL_LIGHT, TEXT, TEXT_SUB, TEXT_MUTED, BORDER, SURFACE
from utils.plotly_theme import TEAL, FONT_COLOR, TICK_COLOR

_PRESET_LABELS = {
    "factor":   "순수 팩터",
    "balanced": "균형",
    "mcap":     "시총 편향",
}


def render(portfolio: Portfolio):
    with st.expander("📅 리밸런싱이란?", expanded=False):
        st.markdown(banner(
            "시간이 지나면 종목별 수익률 차이로 인해 실제 비중이 목표 비중에서 벗어납니다.<br>"
            "반기 또는 분기마다 초과 상승한 종목을 일부 매도하고 비중이 낮아진 종목을 매수해 "
            "목표 비중으로 되돌리는 작업입니다.", "info"
        ), unsafe_allow_html=True)

    if not portfolio.tickers():
        st.info("포트폴리오 탭에서 종목을 먼저 추가하세요.")
        return

    col_rb1, col_rb2, col_rb3 = st.columns([1.5, 1.5, 1.5])
    with col_rb1:
        _saved_rb_preset = portfolio.get_setting("rebal_mcap_preset", "balanced")
        _preset_opts     = list(_PRESET_LABELS.keys())
        _preset_idx      = _preset_opts.index(_saved_rb_preset) if _saved_rb_preset in _preset_opts else 1
        rb_mcap_preset = st.radio(
            "시총 반영",
            options=_preset_opts,
            format_func=lambda k: _PRESET_LABELS[k],
            index=_preset_idx,
            horizontal=False,
            key="rb_mcap_preset",
        )
    with col_rb2:
        _max_rebal     = len(portfolio.tickers())
        _saved_rebal_n = portfolio.get_setting("rebal_top_n", 15)
        rb_top_n = st.number_input(
            "비중 산출 종목 수 (Top N)",
            min_value=1, max_value=_max_rebal,
            value=min(_saved_rebal_n, _max_rebal),
            step=1, key="rb_topn",
        )
    with col_rb3:
        st.markdown('<div style="height:1.6rem"></div>', unsafe_allow_html=True)
        run_rebal = st.button("🔄 리밸런싱 계산 실행", key="btn_rebal", type="primary", width="stretch")

    if run_rebal:
        portfolio.set_setting("rebal_mcap_preset", rb_mcap_preset)
        portfolio.set_setting("rebal_top_n", int(rb_top_n))
        portfolio.save()
        with st.spinner("시세 및 목표 비중 계산 중..."):
            try:
                res_rb = rebalance_weights(
                    holdings=portfolio.holdings,
                    mcap_preset=rb_mcap_preset,
                    top_n=int(rb_top_n),
                )
                st.session_state["rebal_result"] = res_rb
            except Exception as e:
                st.error(f"오류 발생: {e}")

    if "rebal_result" not in st.session_state:
        return

    res_rb      = st.session_state["rebal_result"]
    prices_rb   = res_rb["prices"]
    weights_rb  = res_rb["weights"]
    fx_rb       = res_rb["fx_rate"]
    fx_est_rb   = res_rb.get("fx_estimated", False)
    is_bull_rb  = res_rb["is_bull"]
    all_tickers = portfolio.tickers()

    holdings_rb   = portfolio.holdings
    total_val_krw = 0.0
    curr_val_krw  = {}
    for t in all_tickers:
        try: p = float(prices_rb.get(t, 0) or 0)
        except Exception: p = 0.0
        val = p * holdings_rb.get(t, 0)
        curr_val_krw[t]  = val
        total_val_krw   += val

    regime_text   = "강세장" if is_bull_rb else "약세장"

    st.markdown(f"""
<div class="qpm-rebal-summary">
  <div class="qpm-rebal-card">
    <div class="qpm-rebal-label">시장 국면</div>
    <div class="qpm-rebal-value">{regime_text}</div>
    <div class="qpm-rebal-sub">{"KODEX 200 > MA200" if is_bull_rb else "KODEX 200 < MA200"}</div>
  </div>
  <div class="qpm-rebal-card">
    <div class="qpm-rebal-label">포트폴리오 총액</div>
    <div class="qpm-rebal-value">₩{total_val_krw:,.0f}</div>
  </div>
  <div class="qpm-rebal-card">
    <div class="qpm-rebal-label">거래 단위</div>
    <div class="qpm-rebal-value">1주</div>
    <div class="qpm-rebal-sub">정수 수량</div>
  </div>
</div>
<style>
.qpm-rebal-summary {{
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin: 10px 0 22px;
}}
.qpm-rebal-card {{
  background: var(--qpm-surface, #F7F8FA);
  border: 0;
  border-radius: 8px;
  padding: 14px 15px;
  min-width: 0;
}}
.qpm-rebal-label {{
  font-size: 0.68rem;
  font-weight: 700;
  color: var(--qpm-text-muted, #8A949E);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 6px;
}}
.qpm-rebal-value {{
  font-size: 1.25rem;
  font-weight: 700;
  color: var(--qpm-text, #111827);
  line-height: 1.2;
  overflow-wrap: anywhere;
}}
.qpm-rebal-sub {{
  margin-top: 5px;
  font-size: 0.72rem;
  color: var(--qpm-text-sub, #4B5563);
}}
@media (max-width: 768px) {{
  .qpm-rebal-summary {{
    grid-template-columns: 1fr;
    gap: 8px;
  }}
}}
</style>
""", unsafe_allow_html=True)

    # ── 리밸런싱 테이블 ─────────────────────────────────
    rows_rb = []
    for t in all_tickers:
        try: curr_price = float(prices_rb.get(t, 0) or 0)
        except Exception: curr_price = 0.0
        curr_shares    = int(holdings_rb.get(t, 0) or 0)
        curr_val       = curr_val_krw.get(t, 0.0)
        curr_weight    = (curr_val / total_val_krw) if total_val_krw > 0 else 0.0
        target_weight  = float(weights_rb.get(t, 0.0)) if t in weights_rb.index else 0.0
        target_val_krw = total_val_krw * target_weight
        target_shares  = int(target_val_krw // curr_price) if curr_price > 0 else 0
        diff_shares    = int(target_shares - curr_shares)
        diff_krw       = diff_shares * curr_price

        rows_rb.append({
            "종목명":          portfolio.get_name(t) or t,
            "티커":            t,
            "현재가 (KRW)":    curr_price if curr_price > 0 else None,
            "보유 수량":       curr_shares,
            "현재 비중 (%)":   curr_weight * 100,
            "목표 비중 (%)":   target_weight * 100,
            "비중 차이 (%)":   (target_weight - curr_weight) * 100,
            "조정 수량":       diff_shares,
            "조정 금액 (KRW)": diff_krw,
        })

    st.markdown(section_title("종목별 리밸런싱 내역"), unsafe_allow_html=True)
    st.dataframe(
        pd.DataFrame(rows_rb),
        column_config={
            "현재가 (KRW)":    st.column_config.NumberColumn(format="₩%.0f"),
            "보유 수량":       st.column_config.NumberColumn(format="%d"),
            "현재 비중 (%)":   st.column_config.NumberColumn(format="%.1f%%"),
            "목표 비중 (%)":   st.column_config.NumberColumn(format="%.1f%%"),
            "비중 차이 (%)":   st.column_config.NumberColumn(format="%+.1f%%"),
            "조정 수량":       st.column_config.NumberColumn(format="%+d"),
            "조정 금액 (KRW)": st.column_config.NumberColumn(format="₩%.0f"),
        },
        width="stretch", hide_index=True,
    )

    # ── 매도 / 매수 대상 ────────────────────────────────
    df_rb    = pd.DataFrame(rows_rb)
    sell_df  = df_rb[df_rb["조정 수량"] < -0.0001].copy()
    buy_df   = df_rb[df_rb["조정 수량"] >  0.0001].copy()

    st.markdown(f"""
<style>
.qpm-trade-board {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
  margin-top: 8px;
}}
.qpm-trade-panel {{
  background: var(--qpm-bg, #FFFFFF);
  border: 0;
  border-radius: 0;
  min-width: 0;
}}
.qpm-trade-head {{
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 0 0 12px;
  border-bottom: 0.5px solid var(--qpm-border, {BORDER});
}}
.qpm-trade-eyebrow {{
  font-size: 11px;
  font-weight: 700;
  color: var(--qpm-text-muted, {TEXT_MUTED});
  text-transform: uppercase;
  letter-spacing: 0.3px;
}}
.qpm-trade-title {{
  margin-top: 3px;
  font-size: 18px;
  font-weight: 700;
  color: var(--qpm-text, {TEXT});
  letter-spacing: 0;
}}
.qpm-trade-total {{
  text-align: right;
  font-size: 18px;
  font-weight: 700;
  color: var(--qpm-text, {TEXT});
  white-space: nowrap;
}}
.qpm-trade-sub {{
  margin-top: 3px;
  font-size: 11px;
  color: var(--qpm-text-muted, {TEXT_MUTED});
}}
.qpm-trade-list {{
  display: flex;
  flex-direction: column;
}}
.qpm-trade-card {{
  display: grid;
  grid-template-columns: 38px minmax(0, 1fr) auto;
  align-items: center;
  gap: 11px;
  padding: 12px 0;
  border-bottom: 0.5px solid var(--qpm-border, {BORDER});
}}
.qpm-trade-card:last-child {{ border-bottom: 0; }}
.qpm-trade-icon {{
  width: 34px;
  height: 34px;
  border-radius: 9px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  font-weight: 700;
  overflow: hidden;
}}
.qpm-sell .qpm-trade-icon {{ background: var(--qpm-danger-bg, #FFF2F2); color: var(--qpm-danger, #A32D2D); }}
.qpm-buy .qpm-trade-icon {{ background: var(--qpm-teal-light, {TEAL_LIGHT}); color: var(--qpm-teal-dark, {TEAL_DARK}); }}
.qpm-trade-ticker {{
  font-size: 14px;
  font-weight: 700;
  color: var(--qpm-text, {TEXT});
}}
.qpm-trade-meta {{
  margin-top: 3px;
  font-size: 11px;
  color: var(--qpm-text-sub, {TEXT_SUB});
}}
.qpm-trade-amount {{
  text-align: right;
  font-size: 13px;
  font-weight: 700;
  color: var(--qpm-text, {TEXT});
}}
.qpm-trade-shares {{
  margin-top: 3px;
  font-size: 11px;
  color: var(--qpm-text-muted, {TEXT_MUTED});
}}
.qpm-weight-bar {{
  position: relative;
  height: 5px;
  margin-top: 8px;
  border-radius: 99px;
  background: var(--qpm-bar-bg, #EDF0F2);
  overflow: hidden;
}}
.qpm-weight-current,
.qpm-weight-target {{
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  border-radius: 99px;
}}
.qpm-weight-current {{ background: var(--qpm-bar-current, #D7DDE2); }}
.qpm-weight-target {{ background: var(--qpm-teal, {TEAL}); opacity: 0.9; }}
.qpm-empty-trade {{
  padding: 18px 0;
  color: var(--qpm-text-sub, {TEXT_SUB});
  font-size: 13px;
  border-bottom: 0.5px solid var(--qpm-border, {BORDER});
}}
@media (max-width: 768px) {{
  .qpm-trade-board {{ grid-template-columns: 1fr; }}
}}
</style>
""", unsafe_allow_html=True)

    def _trade_panel_html(kind: str, title: str, df: pd.DataFrame) -> str:
        is_sell = kind == "sell"
        action = "매도" if is_sell else "매수"
        total = abs(float(df["조정 금액 (KRW)"].sum())) if not df.empty else 0.0
        count = len(df)
        tone_cls = "qpm-sell" if is_sell else "qpm-buy"
        rows_html = ""

        if df.empty:
            rows_html = f'<div class="qpm-empty-trade">지금은 더 {action}할 종목이 없어요.</div>'
        else:
            sort_col = "조정 금액 (KRW)"
            ordered = df.assign(_abs=df[sort_col].abs()).sort_values("_abs", ascending=False)
            for _, row in ordered.iterrows():
                current_w = max(float(row["현재 비중 (%)"]), 0.0)
                target_w = max(float(row["목표 비중 (%)"]), 0.0)
                current_bar = min(current_w, 100.0)
                target_bar = min(target_w, 100.0)
                amount = abs(float(row["조정 금액 (KRW)"]))
                shares = abs(int(row["조정 수량"]))
                ticker = row["티커"]
                display_name = row.get("종목명") or ticker
                display_link = stock_link(ticker, display_name)
                logo_url = portfolio.get_logo(ticker)
                if logo_url:
                    icon_html = (
                        f'<div class="qpm-trade-icon">'
                        f'<img src="{logo_url}" alt="{ticker}" '
                        f'style="width:100%;height:100%;object-fit:contain;padding:5px;border-radius:inherit" '
                        f'onerror="this.remove();this.parentElement.textContent=\'{ticker[:2]}\'">'
                        f'</div>'
                    )
                else:
                    icon_html = f'<div class="qpm-trade-icon">{ticker[:2]}</div>'
                rows_html += f"""
<div class="qpm-trade-card">
  {icon_html}
  <div style="min-width:0">
    <div class="qpm-trade-ticker">{display_link}</div>
    <div class="qpm-trade-meta">{ticker} · 현재 {current_w:.1f}% → 목표 {target_w:.1f}%</div>
    <div class="qpm-weight-bar">
      <span class="qpm-weight-current" style="width:{current_bar:.1f}%"></span>
      <span class="qpm-weight-target" style="width:{target_bar:.1f}%"></span>
    </div>
  </div>
  <div>
    <div class="qpm-trade-amount">₩{amount:,.0f}</div>
    <div class="qpm-trade-shares">{shares:,}주</div>
  </div>
</div>"""

        return f"""
<div class="qpm-trade-panel {tone_cls}">
  <div class="qpm-trade-head">
    <div>
      <div class="qpm-trade-eyebrow">리밸런싱</div>
      <div class="qpm-trade-title">{title}</div>
    </div>
    <div>
      <div class="qpm-trade-total">₩{total:,.0f}</div>
      <div class="qpm-trade-sub">{count}개 종목</div>
    </div>
  </div>
  <div class="qpm-trade-list">{rows_html}</div>
</div>"""

    col_sell, col_buy = st.columns(2)
    with col_sell:
        st.markdown(_trade_panel_html("sell", "이만큼 더 팔아야 해요", sell_df), unsafe_allow_html=True)
    with col_buy:
        st.markdown(_trade_panel_html("buy", "이만큼 더 사야 해요", buy_df), unsafe_allow_html=True)

    st.caption("회색 막대는 현재 비중, 딥 그린 막대는 목표 비중입니다.")
