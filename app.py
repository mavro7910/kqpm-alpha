"""app.py — QPM Alpha 진입점"""

import hashlib, sys, os
from pathlib import Path

import streamlit as st
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(os.getcwd())))

from core.portfolio import Portfolio
from utils.ui import inject_all, HEADER_GRAD, BORDER, TEAL, TEXT, TEXT_SUB, TEXT_MUTED, SURFACE, ICON_B64
from tabs import (
    tab_portfolio, tab_ai_signal, tab_buyrec,
    tab_backtest, tab_sell_signal, tab_rebalance, tab_settings,
)

_icon_path = Path(os.getcwd()) / "assets" / "icon.png"
try:    _icon = Image.open(_icon_path).resize((64, 64))
except: _icon = "📊"

st.set_page_config(
    page_title="KQPM Alpha",
    page_icon=_icon,
    layout="wide",
    initial_sidebar_state="collapsed",
)
st.session_state.setdefault("qpm_dark_mode", False)
inject_all()


def email_to_key(email: str) -> str:
    return hashlib.sha256(email.lower().encode()).hexdigest()[:16]

def invalidate_cache(*keys):
    for k in keys: st.session_state.pop(k, None)


# ── 로그인 게이트 ────────────────────────────────────────
if not st.user.is_logged_in:
    st.markdown(f"""
<div style="background:#FFFFFF;border-bottom:0.5px solid {BORDER};
            padding:18px 0 16px;margin-bottom:22px;display:flex;align-items:center;gap:12px">
  <img src="{ICON_B64}" width="44" height="44"
       style="border-radius:10px"
       onerror="this.style.display='none'">
  <div>
    <div style="font-size:1.4rem;font-weight:700;color:{TEXT}">
      KQPM <span style="color:{TEAL}">Alpha</span>
    </div>
    <div style="font-size:0.78rem;color:{TEXT_MUTED};margin-top:2px">
      Korean Quantitative Portfolio Manager · KRX Factor Strategy
    </div>
  </div>
</div>
""", unsafe_allow_html=True)
    st.markdown(
        f'<div style="background:#eef5ff;border:0.5px solid rgba(60,120,210,0.18);border-radius:10px;'
        f'padding:12px 16px;color:#2a5090;font-size:0.84rem;margin-bottom:16px">'
        f'🔐 <b>로그인이 필요합니다</b><br>'
        f'Google 계정으로 로그인하면 어떤 기기에서든 동일한 포트폴리오를 사용할 수 있습니다.</div>',
        unsafe_allow_html=True,
    )
    col_login, _ = st.columns([1, 3])
    with col_login:
        if st.button("Google로 로그인", key="btn_login", type="primary"):
            st.login("google")
    st.stop()


# ── 포트폴리오 로드 ───────────────────────────────────────
_user_email = st.user.email or st.user.get("sub", "unknown")
_user_name  = st.user.get("name", _user_email)
_file_key   = email_to_key(_user_email)

if "portfolio" not in st.session_state or st.session_state.get("_user_email") != _user_email:
    data_path = Path(os.getcwd()) / "data" / f"portfolio_{_file_key}.json"
    st.session_state.portfolio      = Portfolio(path=data_path)
    st.session_state["_user_email"] = _user_email
    st.session_state["_qpm_theme_loaded"] = False
    invalidate_cache("prices_data", "buy_result", "bt_result", "rebal_result", "sell_result")

portfolio: Portfolio = st.session_state.portfolio

if not st.session_state.get("_qpm_theme_loaded", False):
    st.session_state["qpm_dark_mode"] = bool(portfolio.get_setting("qpm_dark_mode", False))
    st.session_state["_qpm_theme_loaded"] = True
    inject_all()

# ── API 키 자동 로드 ──────────────────────────────────────
from utils.ai_client import (
    has_api_key, set_api_key, has_finnhub_key, set_finnhub_key,
    has_marketaux_key, set_marketaux_key,
)
from core.secrets_store import load_api_key, load_finnhub_key, load_marketaux_key

if not has_api_key():
    _k, _ = load_api_key(_file_key)
    if _k: set_api_key(_k)
if not has_finnhub_key():
    _k, _ = load_finnhub_key(_file_key)
    if _k: set_finnhub_key(_k)
if not has_marketaux_key():
    _k, _ = load_marketaux_key(_file_key)
    if _k: set_marketaux_key(_k)


# ── 헤더 ─────────────────────────────────────────────────
initials = "".join(p[0].upper() for p in _user_name.split()[:2]) if _user_name else "U"

def _save_dark_mode():
    portfolio.set_setting("qpm_dark_mode", bool(st.session_state.get("qpm_dark_mode", False)))
    portfolio.save()

st.markdown(f"""
<div class="qpm-app-header">
  <div class="qpm-logo">
    <img src="{ICON_B64}" alt="KQPM" onerror="this.style.display='none'">
    <div>
      <div class="qpm-logo-title">KQPM <span>Alpha</span></div>
      <div class="qpm-logo-subtitle">Korean Quantitative Portfolio Manager</div>
    </div>
  </div>
  <div class="qpm-user-pill">
    <div class="qpm-avatar">{initials}</div>
    <span>{_user_email}</span>
  </div>
</div>
""", unsafe_allow_html=True)

# 로그아웃/테마 토글 — 헤더 아래에 작게 배치
_hcol1, _hcol2, _hcol3 = st.columns([6, 1.35, 1])
with _hcol2:
    st.toggle("다크 모드", key="qpm_dark_mode", on_change=_save_dark_mode)
with _hcol3:
    if st.button("로그아웃", key="btn_logout"):
        st.logout()

# ── 탭 ───────────────────────────────────────────────────
tab_port, tab_sig, tab_buy, tab_bt, tab_sell, tab_rebal, tab_cfg = st.tabs([
    "포트폴리오", "AI 시그널", "매수 추천",
    "백테스트", "매도 신호", "리밸런싱", "설정",
])

with tab_port:  tab_portfolio.render(portfolio)
with tab_sig:   tab_ai_signal.render(portfolio, _file_key)
with tab_buy:   tab_buyrec.render(portfolio)
with tab_bt:    tab_backtest.render(portfolio)
with tab_sell:  tab_sell_signal.render(portfolio)
with tab_rebal: tab_rebalance.render(portfolio)
with tab_cfg:   tab_settings.render(portfolio, _user_email, _user_name, _file_key)

st.markdown("""
<div class="qpm-footer-status">
  <span class="qpm-status-dot"></span>
  <span>Naver Finance 국장 시세 · KRX 정수 수량</span>
</div>
""", unsafe_allow_html=True)
