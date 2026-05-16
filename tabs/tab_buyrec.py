"""tabs/tab_buyrec.py — 매수 추천 탭"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.portfolio import Portfolio
from core.strategy import buy_recommendation
from utils.ui import section_title, metric_card, banner, badge, TEAL, TEAL_DARK, TEAL_LIGHT, TEXT, TEXT_SUB, TEXT_MUTED, BORDER, SURFACE, GOLD, GOLD_LIGHT, RED
from utils.plotly_theme import base_layout, TEAL as PT, BLUE, AMBER

_PRESET_LABELS = {"factor":"순수 팩터","balanced":"균형","mcap":"시총 편향"}
_PRESET_DESC   = {"factor":"모멘텀·변동성 팩터만 (γ=0%)","balanced":"팩터 85% + 시총 15%","mcap":"팩터 70% + 시총 30%"}

def safe_sum(obj):
    if isinstance(obj, pd.Series): return float(obj.sum())
    if isinstance(obj, dict): return float(sum(obj.values()))
    try: return float(obj)
    except: return 0.0

def safe_get(obj, key, default=0.0):
    try:
        if isinstance(obj, pd.Series): return float(obj.get(key, default))
        if isinstance(obj, dict): return float(obj.get(key, default))
        return default
    except: return default


def render(portfolio: Portfolio):
    col_b, col_m, col_run = st.columns([2.5, 2, 1.5])
    with col_b:
        budget = st.number_input("투자 금액 (KRW)", min_value=10_000, max_value=100_000_000,
                                 value=portfolio.weekly_budget, step=10_000)
    with col_m:
        _saved  = portfolio.get_setting("mcap_preset","balanced")
        _opts   = list(_PRESET_LABELS.keys())
        _idx    = _opts.index(_saved) if _saved in _opts else 1
        mcap_preset = st.radio("시총 반영", options=_opts,
                               format_func=lambda k: _PRESET_LABELS[k], index=_idx,
                               horizontal=False,
                               help="\n".join(f"**{_PRESET_LABELS[k]}**: {_PRESET_DESC[k]}" for k in _opts))
    with col_run:
        st.markdown('<div style="height:1.6rem"></div>', unsafe_allow_html=True)
        run_buy = st.button("▶ 매수 추천 실행", key="btn_buy", type="primary")

    _max_n    = len(portfolio.tickers()) if portfolio.tickers() else 20
    _saved_n  = portfolio.get_setting("top_n", 10)
    n_tickers = st.number_input("추천 종목 수", min_value=1, max_value=_max_n,
                                value=min(_saved_n,_max_n), step=1,
                                help="포트폴리오 내 종목 중 상위 N개에 예산을 배분합니다.")

    with st.expander("📐 KQPM Alpha 알고리즘 개요", expanded=False):
        st.markdown(banner(
            "<b>① 시장 국면</b> — KODEX 200 현재가 vs MA200 → 강세장 / 약세장<br>"
            "<b>② 팩터 Z-score</b> — 모멘텀(21·63·126·252일 가중) + 변동성역수(60일)<br>"
            "<b>③ 국면별 가중합</b> — 강세장: 모멘텀 70% + 변동성역수 30% / 약세장: 40%+60%<br>"
            "<b>④ ReLU → 정규화 → 25% 캡</b>", "info"), unsafe_allow_html=True)

    if run_buy:
        if not portfolio.tickers():
            st.error("포트폴리오 탭에서 종목을 먼저 입력하세요.")
        else:
            portfolio.weekly_budget = budget
            portfolio.set_setting("mcap_preset", mcap_preset)
            portfolio.set_setting("top_n", int(n_tickers))
            portfolio.save()
            with st.spinner("시장 데이터 수집 및 팩터 계산 중..."):
                try:
                    res = buy_recommendation(holdings=portfolio.holdings, budget_krw=budget,
                                             mcap_preset=mcap_preset, top_n=int(n_tickers))
                    st.session_state["buy_result"] = res
                except Exception as e:
                    st.error(f"오류 발생: {e}")

    if "buy_result" not in st.session_state:
        return

    res        = st.session_state["buy_result"]
    tickers_r  = res["tickers"]
    weights    = res["weights"]
    buy_krw    = res["buy_krw"]
    buy_extra  = res["buy_usd"]
    buy_shares = res["buy_shares"]
    prices_r   = res["prices"]
    fx_est     = res.get("fx_estimated", False)
    mcap_ok    = res.get("mcap_ok", True)
    mcap_gamma = res.get("mcap_gamma", 0.0)
    mcap_pname = res.get("mcap_preset","balanced")
    is_bull    = res["is_bull"]
    total_krw  = res.get("total_value_krw", res["total_value_usd"])

    if mcap_gamma > 0 and not mcap_ok:
        st.markdown(banner("⚠️ 시가총액 조회 실패 — 순수 팩터로 자동 전환되었습니다.", "warn"), unsafe_allow_html=True)

    # ── 메트릭 그리드 ─────────────────────────────────────
    regime_text = "강세장" if is_bull else "약세장"
    total_buy   = safe_sum(buy_krw)

    st.markdown(f"""
<div class="qpm-metric-grid" style="display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:14px 0">
  {metric_card("시장 국면", regime_text, f'{"KODEX 200 > MA200" if is_bull else "KODEX 200 < MA200"}', "#1457A8" if is_bull else "#D94B4B")}
  {metric_card("시총 반영", _PRESET_LABELS.get(mcap_pname,mcap_pname), _PRESET_DESC.get(mcap_pname,""))}
  {metric_card("거래 단위", "1주", "국장 정수 수량")}
  {metric_card("포트폴리오 총액", f"₩{total_krw:,.0f}", "현재가 기준")}
</div>
""", unsafe_allow_html=True)

    # ── 매수 추천 카드 (HTML) ─────────────────────────────
    st.markdown(section_title("이번 주 매수 추천"), unsafe_allow_html=True)
    rows_html = ""
    _colors = [TEAL,"#4a90d9","#c9873a","#8b72c8","#5ab87a","#e05252","#a0b4b2","#3a8fc8"]
    for i, t in enumerate(tickers_r):
        w   = safe_get(weights, t) * 100
        krw = safe_get(buy_krw, t)
        shr = safe_get(buy_shares, t)
        c   = _colors[i % len(_colors)]
        logo_url = portfolio.get_logo(t)
        name = portfolio.get_name(t) or t
        if logo_url:
            icon_html = (
                f'<div style="width:32px;height:32px;border-radius:9px;background:#F7F8FA;overflow:hidden;'
                f'display:flex;align-items:center;justify-content:center">'
                f'<img src="{logo_url}" alt="{t}" '
                f'style="width:100%;height:100%;object-fit:contain;padding:5px;border-radius:inherit" '
                f'onerror="this.remove();this.parentElement.textContent=\'{t[:2]}\';'
                f'this.parentElement.style.color=\'{c}\'">'
                f'</div>'
            )
        else:
            icon_html = (
                f'<div style="width:32px;height:32px;border-radius:9px;background:{c}18;color:{c};'
                f'display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:700">{t[:2]}</div>'
            )
        rows_html += f"""
<div class="qpm-rec-row" style="display:grid;grid-template-columns:34px 1fr 52px 90px;
     gap:10px;align-items:center;padding:10px 0;border-bottom:0.5px solid rgba(15,110,86,0.08)">
  {icon_html}
  <div>
    <div style="font-size:13.5px;font-weight:600;color:{TEXT}">{name}</div>
    <div style="font-size:11px;color:{TEXT_MUTED};margin-top:1px">#{i+1} · {t} · {int(shr):,}주</div>
  </div>
  <div style="font-size:13px;font-weight:600;color:{TEAL};text-align:right">{w:.1f}%</div>
  <div class="qpm-rec-amount" style="font-size:12px;color:{TEXT_SUB};text-align:right">₩{krw:,.0f}</div>
</div>"""

    regime_badge = badge("강세장","bull") if is_bull else badge("약세장","bear")
    preset_badge = badge(_PRESET_LABELS.get(mcap_pname,mcap_pname),"gold")

    st.markdown(f"""
<div style="background:#FFFFFF;border:0;border-radius:0;padding:15px 0;margin:4px 0">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:13px">
    <div style="display:flex;align-items:center;gap:7px">
      {regime_badge} {preset_badge}
    </div>
    <span style="font-size:11px;color:{TEXT_MUTED}">예산 ₩{budget:,} · KQPM Alpha</span>
  </div>
  {rows_html}
  <div style="margin-top:10px;padding-top:10px;border-top:0.5px solid rgba(15,110,86,0.1);
              font-size:13px;font-weight:700;color:{TEAL};text-align:right">
    총 ₩{total_buy:,.0f}
  </div>
</div>
""", unsafe_allow_html=True)

    # ── 상세 테이블 ────────────────────────────────────────
    with st.expander("📊 상세 내역 보기", expanded=False):
        rows = []
        for t in tickers_r:
            try: p = float(prices_r[t])
            except: p = None
            rows.append({"종목명": portfolio.get_name(t) or t, "티커":t, "목표 비중(%)":safe_get(weights,t)*100,
                          "현재가(KRW)":p, "권장 매수금액(KRW)":safe_get(buy_krw,t),
                          "미집행 예산(KRW)":max(safe_get(buy_extra,t) - safe_get(buy_krw,t), 0.0),
                          "매수 수량":safe_get(buy_shares,t)})
        st.dataframe(pd.DataFrame(rows), column_config={
            "목표 비중(%)":  st.column_config.NumberColumn(format="%.1f%%"),
            "현재가(KRW)":   st.column_config.NumberColumn(format="₩%.0f"),
            "권장 매수금액(KRW)": st.column_config.NumberColumn(format="₩%.0f"),
            "미집행 예산(KRW)": st.column_config.NumberColumn(format="₩%.0f"),
            "매수 수량":     st.column_config.NumberColumn(format="%d"),
        }, width="stretch", hide_index=True)

    # ── 파이 차트 ─────────────────────────────────────────
    pie_values = [safe_get(weights,t) for t in tickers_r]
    fig_pie = go.Figure(go.Pie(
        labels=tickers_r, values=pie_values, hole=0.48,
        textinfo="percent", texttemplate="%{percent:.1%}", textposition="inside",
        insidetextorientation="radial",
        marker=dict(colors=_colors[:len(tickers_r)],
                    line=dict(color="rgba(255,255,255,0.7)", width=1.5)),
        hovertemplate="<b>%{label}</b><br>%{percent:.1%}<extra></extra>",
    ))
    fig_pie.update_layout(dict(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=30,b=10,l=10,r=10), height=320,
        font=dict(color=TEXT, size=11), showlegend=True,
        legend=dict(orientation="v", x=1.02, y=0.5,
                    font=dict(color=TEXT, size=11), bgcolor="rgba(0,0,0,0)"),
        title=dict(text="목표 비중", font=dict(color=TEXT_SUB, size=12), x=0.01),
    ))
    st.plotly_chart(fig_pie, width="stretch", key="pie_chart")
