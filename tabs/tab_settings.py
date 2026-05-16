"""tabs/tab_settings.py — 설정 탭"""

import json
import streamlit as st

from core.portfolio import Portfolio
from core.secrets_store import (
    load_api_key, save_api_key, delete_api_key,
    load_finnhub_key, save_finnhub_key, delete_finnhub_key,
    load_marketaux_key, save_marketaux_key, delete_marketaux_key,
)
from utils.ai_client import (
    set_api_key, get_api_key, has_api_key, clear_api_key, validate_api_key,
    set_finnhub_key, get_finnhub_key, has_finnhub_key, clear_finnhub_key, validate_finnhub_key,
    set_marketaux_key, get_marketaux_key, has_marketaux_key, clear_marketaux_key, validate_marketaux_key,
)
from utils.ui import section_title, banner, TEAL, TEXT, TEXT_SUB, TEXT_MUTED, BORDER, SURFACE

_PRESET_LABELS = {"factor":"순수 팩터","balanced":"균형","mcap":"시총 편향"}


def render(portfolio: Portfolio, user_email: str, user_name: str, file_key: str):

    # ── 투자 설정 ─────────────────────────────────────────
    st.markdown(section_title("투자 설정"), unsafe_allow_html=True)
    col_s1, col_s2 = st.columns(2)
    with col_s1:
        new_budget = st.number_input("주간 투자 금액 (KRW)", min_value=10_000, max_value=100_000_000,
                                     value=portfolio.weekly_budget, step=10_000)
    with col_s2:
        new_bm = st.text_input("벤치마크 티커 (쉼표 구분)", value=", ".join(portfolio.benchmarks),
                               placeholder="069500, 229200")
    col_s3, _ = st.columns(2)
    with col_s3:
        _max_n_cfg = len(portfolio.tickers()) if portfolio.tickers() else 20
        _saved_n   = portfolio.get_setting("top_n", 10)
        new_top_n  = st.number_input("기본 추천 종목 수 (Top N)", min_value=1,
                                     max_value=_max_n_cfg, value=min(_saved_n,_max_n_cfg), step=1)

    if st.button("💾 설정 저장", key="btn_save_settings"):
        portfolio.weekly_budget = new_budget
        bms = [b.strip().upper() for b in new_bm.split(",") if b.strip()]
        if not bms:
            st.error("벤치마크를 하나 이상 입력하세요.")
        else:
            portfolio.benchmarks = bms
            portfolio.set_setting("top_n", int(new_top_n))
            portfolio.save()
            st.success("✅ 설정이 저장되었습니다!")

    # ── 계정 정보 ─────────────────────────────────────────
    st.markdown(section_title("계정 정보"), unsafe_allow_html=True)
    st.markdown(banner(
        f'👤 <b>로그인 계정:</b> {user_name} ({user_email})<br>'
        f'💾 <b>데이터 저장:</b> <code>portfolio_{file_key}.json</code>', "info"
    ), unsafe_allow_html=True)

    # ── API 키 관리 ───────────────────────────────────────
    st.markdown(section_title("API 키 관리"), unsafe_allow_html=True)

    if "key_save_msg" in st.session_state:
        msg_type, msg_text = st.session_state.pop("key_save_msg")
        if msg_type == "success": st.success(msg_text)
        else: st.error(msg_text)

    # 자동 로드
    if not has_api_key():
        stored_key, err = load_api_key(file_key)
        if stored_key: set_api_key(stored_key)
        elif err: st.warning(f"Gemini 키 자동 로드 실패: {err}")
    if not has_finnhub_key():
        stored_fh, _ = load_finnhub_key(file_key)
        if stored_fh: set_finnhub_key(stored_fh)
    if not has_marketaux_key():
        stored_mx, _ = load_marketaux_key(file_key)
        if stored_mx: set_marketaux_key(stored_mx)

    # 상태 표시 (HTML 카드)
    def key_status(name, has, get_fn, optional=False):
        if has():
            masked = "●●●●" + get_fn()[-4:]
            return f'<div style="background:#f0faf7;border:0.5px solid rgba(15,110,86,0.28);border-radius:10px;padding:10px 14px;font-size:0.82rem;color:#0e7a6e">✅ <b>{name}</b> <code style="font-size:0.75rem">{masked}</code></div>'
        else:
            tag = " (선택)" if optional else ""
            return f'<div style="background:#fff8f0;border:0.5px solid rgba(200,120,40,0.28);border-radius:10px;padding:10px 14px;font-size:0.82rem;color:#9a5a18">⚠️ <b>{name}</b>{tag} 키 없음</div>'

    col_g, col_f, col_mx = st.columns(3)
    with col_g:  st.markdown(key_status("Gemini",    has_api_key,      get_api_key),          unsafe_allow_html=True)
    with col_f:  st.markdown(key_status("Finnhub",   has_finnhub_key,  get_finnhub_key),       unsafe_allow_html=True)
    with col_mx: st.markdown(key_status("Marketaux", has_marketaux_key, get_marketaux_key, True), unsafe_allow_html=True)

    # 입력 폼
    col_k1, col_k2, col_k3 = st.columns(3)
    with col_k1:
        new_gemini = st.text_input("Gemini API 키", type="password", placeholder="AIza...",
                                   help="aistudio.google.com 에서 무료 발급", key="inp_gemini_key")
    with col_k2:
        new_finnhub = st.text_input("Finnhub API 키", type="password", placeholder="d1abc123...",
                                    help="finnhub.io/register 에서 무료 발급", key="inp_finnhub_key")
    with col_k3:
        new_marketaux = st.text_input("Marketaux API 키 (선택)", type="password",
                                      placeholder="marketaux 키...", key="inp_marketaux_key")

    col_save, col_del = st.columns([3,1])
    with col_save:
        if st.button("✅ 검증 후 저장", key="btn_save_keys", width="stretch"):
            errors, saved = [], []
            if new_gemini.strip():
                valid, err = validate_api_key(new_gemini.strip())
                if valid:
                    set_api_key(new_gemini.strip())
                    ok, se = save_api_key(file_key, new_gemini.strip())
                    if ok: saved.append("Gemini")
                    else: errors.append(f"Gemini 저장 실패: {se}")
                else: errors.append(f"Gemini: {err}")
            if new_finnhub.strip():
                valid_fh, err_fh = validate_finnhub_key(new_finnhub.strip())
                if valid_fh:
                    set_finnhub_key(new_finnhub.strip())
                    ok_fh, se_fh = save_finnhub_key(file_key, new_finnhub.strip())
                    if ok_fh: saved.append("Finnhub")
                    else: errors.append(f"Finnhub 저장 실패: {se_fh}")
                else: errors.append(f"Finnhub: {err_fh}")
            if new_marketaux.strip():
                valid_mx, err_mx = validate_marketaux_key(new_marketaux.strip())
                if valid_mx:
                    set_marketaux_key(new_marketaux.strip())
                    ok_mx, se_mx = save_marketaux_key(file_key, new_marketaux.strip())
                    if ok_mx: saved.append("Marketaux")
                    else: errors.append(f"Marketaux 저장 실패: {se_mx}")
                else: errors.append(f"Marketaux: {err_mx}")

            if not any([new_gemini.strip(), new_finnhub.strip(), new_marketaux.strip()]):
                st.error("키를 하나 이상 입력하세요.")
            else:
                if saved:   st.session_state["key_save_msg"] = ("success", f"✅ {', '.join(saved)} 키 저장 완료!")
                if errors:  st.session_state["key_save_msg"] = ("error", "\n".join(errors))
                st.rerun()
    with col_del:
        if has_api_key() or has_finnhub_key() or has_marketaux_key():
            if st.button("🗑️ 전체 삭제", key="btn_del_keys", width="stretch"):
                for fn in [clear_api_key, clear_finnhub_key, clear_marketaux_key]: fn()
                for fn in [delete_api_key, delete_finnhub_key, delete_marketaux_key]: fn(file_key)
                st.rerun()

    st.markdown(banner(
        '🔒 키는 암호화되어 안전하게 저장됩니다.<br>'
        f'· <a href="https://aistudio.google.com/app/apikey" target="_blank" style="color:{TEAL}">Gemini 발급</a>'
        f' &nbsp;·&nbsp; <a href="https://finnhub.io/register" target="_blank" style="color:{TEAL}">Finnhub 발급</a>'
        f' &nbsp;·&nbsp; <a href="https://www.marketaux.com/register" target="_blank" style="color:{TEAL}">Marketaux 발급</a>', "info"
    ), unsafe_allow_html=True)

    # ── JSON 내보내기/불러오기 ────────────────────────────
    st.markdown(section_title("포트폴리오 백업"), unsafe_allow_html=True)
    json_str = json.dumps(portfolio._data, ensure_ascii=False, indent=2)
    st.download_button("⬇️ portfolio.json 다운로드", data=json_str,
                       file_name="portfolio.json", mime="application/json")

    st.markdown(section_title("포트폴리오 복원"), unsafe_allow_html=True)
    uploaded      = st.file_uploader("portfolio.json 업로드", type=["json"], key="json_uploader")
    last_uploaded = st.session_state.get("_last_uploaded_name")
    if uploaded is not None and uploaded.name != last_uploaded:
        try:
            raw_bytes = uploaded.read()
            if not raw_bytes: st.error("업로드된 파일이 비어 있습니다.")
            else:
                data = json.loads(raw_bytes.decode("utf-8"))
                if not isinstance(data, dict) or "holdings" not in data:
                    st.error("올바른 portfolio.json 형식이 아닙니다.")
                else:
                    portfolio._data = data
                    portfolio.save()
                    for k in ("prices_data","buy_result","bt_result"):
                        st.session_state.pop(k, None)
                    st.session_state["_last_uploaded_name"] = uploaded.name
                    st.success("✅ 포트폴리오를 불러왔습니다!")
        except Exception as e:
            st.error(f"파일 읽기 오류: {e}")
