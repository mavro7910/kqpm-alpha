"""
tabs/tab_ai_signal.py

AI 시그널 탭.
- Gemini API로 보유 종목 뉴스 분석
- HTML 컴포넌트로 렌더링 (st.components.v1.html)
- 하루 단위 session_state 캐시로 API 비용 절약
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
# date, datetime, timedelta를 가져옵니다.
from datetime import date, datetime, timedelta

import streamlit as st
import streamlit.components.v1 as components

from core.portfolio import Portfolio
from utils.ai_client import (
    analyze_portfolio_signals,
    get_api_key, has_api_key, set_api_key,
    get_finnhub_key, set_finnhub_key, has_finnhub_key,
    get_marketaux_key, set_marketaux_key, has_marketaux_key,
)
from core.secrets_store import (
    load_api_key, load_signal_cache, save_signal_cache,
    load_finnhub_key, load_marketaux_key,
)


# ─────────────────────────────────────────────
# 헬퍼 함수: 한국 시간 생성
# ─────────────────────────────────────────────
def get_kst_now():
    """서버(UTC) 시간에 9시간을 더해 한국 시간을 반환"""
    return datetime.utcnow() + timedelta(hours=9)


# ─────────────────────────────────────────────
# 캐시 (Supabase + session_state 이중화)
# ─────────────────────────────────────────────

def _get_cached(uid: str) -> list | None:
    # date.today() 대신 한국 시간 기준 날짜로 키 생성
    kst_today = get_kst_now().date().isoformat()
    key = f"signal_cache_{kst_today}"
    
    if key in st.session_state:
        return st.session_state[key]
    
    data, err = load_signal_cache(uid)
    if data:
        st.session_state[key] = data
    return data


def _set_cached(uid: str, data: list):
    # 저장할 때도 한국 시간 기준 날짜로 키 생성
    kst_today = get_kst_now().date().isoformat()
    key = f"signal_cache_{kst_today}"
    
    st.session_state[key] = data
    ok, err = save_signal_cache(uid, data)
    if not ok:
        st.warning(f"⚠️ 분석 결과 저장 실패: {err}")


# ─────────────────────────────────────────────
# 렌더 진입점
# ─────────────────────────────────────────────

def render(portfolio: Portfolio, file_key: str):
    if not has_finnhub_key():
        stored_fh, _ = load_finnhub_key(file_key)
        if stored_fh:
            set_finnhub_key(stored_fh)

    if not has_marketaux_key():
        stored_mx, _ = load_marketaux_key(file_key)
        if stored_mx:
            set_marketaux_key(stored_mx)

    if not has_api_key():
        stored, err = load_api_key(file_key)
        if stored:
            set_api_key(stored)
        else:
            col_key, col_keybtn = st.columns([4, 1])
            with col_key:
                if err:
                    st.warning(f"키 자동 로드 실패: {err}")
                else:
                    st.markdown(
                        '<div class="warn-banner">🔑 API 키가 없습니다. 설정 탭에서 등록하거나 불러오세요.</div>',
                        unsafe_allow_html=True,
                    )
            with col_keybtn:
                st.markdown('<div style="height:0.4rem"></div>', unsafe_allow_html=True)
                if st.button("🔑 키 불러오기", key="btn_load_key", width="stretch"):
                    stored2, err2 = load_api_key(file_key)
                    if stored2:
                        set_api_key(stored2)
                        st.rerun()
                    elif err2:
                        st.error(f"불러오기 실패: {err2}")
                    else:
                        st.error("저장된 키가 없습니다. 설정 탭에서 등록하세요.")

    if not portfolio.tickers():
        st.markdown(
            '<div class="info-banner">📋 포트폴리오 탭에서 종목을 먼저 추가하세요.</div>',
            unsafe_allow_html=True,
        )
        return

    active_holdings = {t: s for t, s in portfolio.holdings.items() if s > 0}
    if not active_holdings:
        st.markdown(
            '<div class="warn-banner">⚠️ 보유 중인 종목이 없어요. 포트폴리오 탭에서 수량을 입력해주세요.</div>',
            unsafe_allow_html=True,
        )
        return

    if not has_api_key():
        st.markdown(
            '<div class="warn-banner">'
            '🔑 <b>Gemini API 키가 필요합니다.</b><br>'
            '설정 탭에서 API 키를 입력하세요.<br>'
            '<a href="https://aistudio.google.com/app/apikey" target="_blank" '
            'style="color:#f0a862">Google AI Studio에서 무료 발급 →</a>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    cached = _get_cached(file_key)
    ticker_count = len(portfolio.tickers())

    if cached:
        analyzed_count = len(cached)
        # 캐시된 데이터에서 날짜와 시간을 가져옴
        analyzed_date  = cached[0].get("analyzed_date", "-") if cached else "-"
        analyzed_time  = cached[0].get("analyzed_time", "") if cached else ""
        time_str = f" {analyzed_time}" if analyzed_time else ""
        st.markdown(
            f'<div class="success-banner">'
            f'✅ 분석 결과 로드됨 · <b>{analyzed_count}개 종목</b> · '
            f'{analyzed_date}{time_str} 기준'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        active_count = len([t for t, s in portfolio.holdings.items() if s > 0])
        st.markdown(
            f'<div class="info-banner">'
            f'📡 보유 수량이 있는 <b>{active_count}개 종목</b>의 최신 뉴스를 AI가 분석합니다.<br>'
            f'<span style="font-size:0.8rem;color:#5c6f99">'
            f'분석 완료 후 결과가 저장됩니다</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    col_btn1, col_btn2, col_btn3 = st.columns([1, 1, 1])
    with col_btn1:
        run_signal = st.button(
            "🔄 스마트 재분석" if cached else "🔍 시그널 분석",
            key="btn_run_signal",
            width="stretch",
            type="primary",
            help="변동이 큰 종목만 재분석, 나머지는 캐시 사용" if cached else None,
        )
    with col_btn2:
        run_full = st.button(
            "🔃 전체 재분석",
            key="btn_run_full",
            width="stretch",
            disabled=not cached,
            help="모든 종목을 강제로 재분석",
        )
    with col_btn3:
        if st.button("📂 저장된 결과 불러오기", key="btn_load_cache", width="stretch"):
            data, err = load_signal_cache(file_key)
            if data:
                _set_cached(file_key, data)
                st.rerun()
            elif err:
                st.error(f"불러오기 실패: {err}")
            else:
                st.info("저장된 분석 결과가 없습니다.")

    if run_signal:
        _run_analysis(portfolio, file_key, force_full=False)
        cached = _get_cached(file_key)

    if run_full:
        _run_analysis(portfolio, file_key, force_full=True)
        cached = _get_cached(file_key)

    if cached:
        _render_api_status(cached)
        _render_signal_html(cached)


# ─────────────────────────────────────────────
# 분석 실행
# ─────────────────────────────────────────────

def _run_analysis(portfolio: Portfolio, file_key: str, force_full: bool = False):
    api_key      = get_api_key()
    holdings     = {t: s for t, s in portfolio.holdings.items() if s > 0}
    total        = len(holdings)

    # 스마트 캐시: 당일 기존 결과 로드 (강제 전체 재분석이 아닐 때)
    cached_results = None
    if not force_full:
        cached_results = _get_cached(file_key)

    progress_bar = st.progress(0)
    status_text  = st.empty()

    def on_progress(current, total, ticker, item):
        pct = int(current / total * 100) if total > 0 else 0
        progress_bar.progress(pct)
        if ticker == "데이터 수집 중":
            sources = []
            if has_finnhub_key():
                sources.append("Finnhub")
            if has_marketaux_key():
                sources.append("Marketaux")
            if not sources:
                sources.append("Naver Finance")
            status_text.markdown(f"📡 **{'+'.join(sources)}**으로 **{total}개 종목** 데이터 수집 중...")
        elif ticker == "퀀트 지표 계산 중":
            status_text.markdown("📊 **퀀트 지표** 계산 중 (모멘텀/변동성/52주)...")
        elif ticker == "AI 분석 중":
            status_text.markdown("🤖 **Gemini AI** 분석 중...")
        elif item is not None:
            reused = item.get("reused_cache", False)
            tag    = "📋 캐시" if reused else "✅ 완료"
            status_text.markdown(f"{tag} **{ticker}** ({current}/{total})")
        else:
            status_text.markdown(f"🤖 **{ticker}** 분석 중... ({current}/{total})")

    try:
        results = analyze_portfolio_signals(
            holdings=holdings,
            api_key=api_key,
            finnhub_key=get_finnhub_key(),
            marketaux_key=get_marketaux_key(),
            progress_callback=on_progress,
            portfolio=portfolio,
            cached_results=cached_results,
        )

        if results:
            now_kst  = get_kst_now()
            kst_date = now_kst.strftime("%Y-%m-%d")
            kst_time = now_kst.strftime("%H:%M:%S")
            for res in results:
                if not res.get("reused_cache"):
                    res["analyzed_date"] = kst_date
                    res["analyzed_time"] = kst_time

    except Exception as e:
        progress_bar.empty()
        status_text.empty()
        st.error(f"❌ 분석 오류: {e}")
        return

    progress_bar.progress(100)
    reused_count   = sum(1 for r in results if r.get("reused_cache"))
    analyzed_count = len(results) - reused_count
    if reused_count > 0:
        status_text.markdown(
            f"✅ **분석 완료!** 재분석 {analyzed_count}개 · 캐시 재사용 {reused_count}개"
        )
    else:
        status_text.markdown(f"✅ **분석 완료!** {len(results)}개 종목")

    if results:
        _set_cached(file_key, results)
    st.rerun()


# ─────────────────────────────────────────────
# API 상태 패널
# ─────────────────────────────────────────────

def _render_api_status(signals: list[dict]):
    """
    각 종목별 데이터 소스(Finnhub / Marketaux / Naver Finance) 호출 결과를
    접을 수 있는 패널로 표시합니다.
    """
    if not signals:
        return

    # 전체 소스 집계
    source_stats: dict[str, dict[str, int]] = {
        "finnhub":   {"ok": 0, "no_data": 0, "fail": 0, "skip": 0},
        "marketaux": {"ok": 0, "no_data": 0, "fail": 0, "skip": 0},
        "naver":     {"ok": 0, "no_data": 0, "fail": 0, "skip": 0},
    }

    ticker_rows = []
    for item in signals:
        status = item.get("api_status") or {}
        if not status:
            continue
        ticker = item.get("ticker", "?")
        row = {"ticker": ticker}
        for src in ("finnhub", "marketaux", "naver"):
            s = status.get(src, "skip")
            row[src] = s
            source_stats[src][s] = source_stats[src].get(s, 0) + 1
        ticker_rows.append(row)

    if not ticker_rows:
        return

    # 요약 배지 생성
    def _badge(src: str) -> str:
        ok  = source_stats[src]["ok"]
        nd  = source_stats[src]["no_data"]
        fa  = source_stats[src]["fail"]
        sk  = source_stats[src]["skip"]
        total_used = ok + nd + fa
        if sk == len(ticker_rows):
            return f"⬜ {src.capitalize()} — 미사용"
        if fa > 0:
            return f"🔴 {src.capitalize()} — {ok}성공 / {nd}데이터없음 / {fa}실패"
        if nd > 0 and ok == 0:
            return f"🟡 {src.capitalize()} — {nd}개 데이터 없음"
        return f"🟢 {src.capitalize()} — {ok}/{total_used} 성공"

    summary_badges = [_badge(src) for src in ("finnhub", "marketaux", "naver")]

    with st.expander("📡 데이터 소스 상태 확인 (클릭하여 펼치기)", expanded=False):
        # 전체 요약
        for badge in summary_badges:
            color = "#4ade80" if badge.startswith("🟢") else \
                    "#fbbf24" if badge.startswith("🟡") else \
                    "#94a3b8" if badge.startswith("⬜") else "#f87171"
            st.markdown(
                f'<div style="padding:4px 0;font-size:0.85rem;color:{color}">{badge}</div>',
                unsafe_allow_html=True,
            )

        st.markdown('<div style="height:0.5rem"></div>', unsafe_allow_html=True)

        # 티커별 상세 테이블
        _STATUS_ICON = {
            "ok":      "✅",
            "no_data": "⚠️",
            "fail":    "❌",
            "skip":    "—",
            "pending": "⏳",
        }

        import pandas as pd
        df_rows = []
        for row in ticker_rows:
            df_rows.append({
                "티커":       row["ticker"],
                "Finnhub":   _STATUS_ICON.get(row.get("finnhub", "skip"), "—"),
                "Marketaux": _STATUS_ICON.get(row.get("marketaux", "skip"), "—"),
                "Naver":     _STATUS_ICON.get(row.get("naver", "skip"), "—"),
            })

        st.dataframe(
            pd.DataFrame(df_rows),
            hide_index=True,
            width="stretch",
        )

        st.markdown(
            '<div style="font-size:0.75rem;color:#5c6f99;margin-top:6px">'
            '✅ 성공 &nbsp;|&nbsp; ⚠️ 데이터 없음 &nbsp;|&nbsp; ❌ 호출 실패 &nbsp;|&nbsp; — 미사용'
            '</div>',
            unsafe_allow_html=True,
        )


# ─────────────────────────────────────────────
# HTML 렌더링
# ─────────────────────────────────────────────

def _render_signal_html(signals: list[dict]):
    """라이트 파스텔 테마 시그널 카드 UI."""

    signals_json = json.dumps(signals, ensure_ascii=False)
    dark_mode = st.session_state.get("qpm_dark_mode", False)
    pal = {
        "bg": "#101413" if dark_mode else "#FFFFFF",
        "surface": "#181D1B" if dark_mode else "#FFFFFF",
        "surface2": "#202623" if dark_mode else "#F7F8FA",
        "border": "#29312E" if dark_mode else "#EDF0F2",
        "border2": "#232B28" if dark_mode else "#F1F3F5",
        "text": "#F2F5F4" if dark_mode else "#111827",
        "text_sub": "#B9C3BF" if dark_mode else "#4B5563",
        "text_muted": "#7F8B87" if dark_mode else "#8A949E",
        "teal": "#7DADEA" if dark_mode else "#1457A8",
        "teal_dark": "#B5F3E2" if dark_mode else "#085041",
        "teal_light": "#14362D" if dark_mode else "#E1F5EE",
        "gold_light": "#322B18" if dark_mode else "#fdf4e0",
        "danger_bg": "#341C1D" if dark_mode else "#fdeaea",
        "empty_bg": "#1C2421" if dark_mode else "rgba(15,110,86,0.06)",
    }

    html = f"""
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  :root {{
    --bg:          {pal["bg"]};
    --surface:     {pal["surface"]};
    --surface2:    {pal["surface2"]};
    --border:      {pal["border"]};
    --border2:     {pal["border2"]};
    --text:        {pal["text"]};
    --text-sub:    {pal["text_sub"]};
    --text-muted:  {pal["text_muted"]};
    --teal:        {pal["teal"]};
    --teal-dark:   {pal["teal_dark"]};
    --teal-light:  {pal["teal_light"]};
    --up:          #e05252;
    --down:        #4a90d9;
    --gold:        #b8922a;
    --gold-light:  {pal["gold_light"]};
    --green:       #1457A8;
    --yellow:      #c9873a;
    --red:         #e05252;
  }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 0 0 24px;
  }}

  /* ── 필터 탭 ── */
  .filter-bar {{
    display: flex; gap: 6px; padding: 12px 16px 8px; flex-wrap: wrap;
    background: var(--bg);
    position: sticky; top: 0; z-index: 10;
  }}
  .filter-btn {{
    padding: 5px 14px; border-radius: 20px; font-size: 12px; font-weight: 600;
    cursor: pointer; border: 0.5px solid var(--border);
    color: var(--text-sub); background: var(--surface);
    font-family: inherit; transition: all 0.15s;
  }}
  .filter-btn.active {{
    background: var(--teal-light); border-color: var(--teal); color: var(--teal-dark);
  }}

  /* ── 카드 ── */
  .card-list {{ padding: 0 16px; display: flex; flex-direction: column; gap: 8px; }}

  .signal-card {{
    background: var(--surface);
    border: 0.5px solid var(--border);
    border-radius: 14px;
    cursor: pointer;
    transition: all 0.18s;
    overflow: hidden;
    position: relative;
  }}
  .signal-card::before {{
    content: ''; position: absolute; left: 0; top: 0; bottom: 0;
    width: 3px; border-radius: 14px 0 0 14px;
  }}
  .signal-card.up::before    {{ background: var(--up); }}
  .signal-card.down::before  {{ background: var(--down); }}
  .signal-card.neutral::before {{ background: var(--text-muted); }}
  .signal-card:hover {{ border-color: var(--teal); box-shadow: 0 2px 12px rgba(15,110,86,0.1); transform: translateY(-1px); }}
  .signal-card.open  {{ border-color: var(--teal); border-radius: 14px 14px 0 0; border-bottom-color: transparent; }}

  /* ── 카드 헤더 ── */
  .card-head {{ display: flex; align-items: center; padding: 12px 14px; gap: 11px; }}
  .ticker-icon {{
    width: 36px; height: 36px; border-radius: 10px;
    display: flex; align-items: center; justify-content: center;
    font-size: 11px; font-weight: 700; flex-shrink: 0;
    overflow: hidden;
  }}
  .card-body {{ flex: 1; min-width: 0; }}
  .card-row1 {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 3px; }}
  .ticker-name {{ font-size: 14.5px; font-weight: 700; color: var(--text); }}
  .ticker-shares {{ font-size: 11px; color: var(--text-muted); margin-left: 5px; font-weight: 400; }}
  .rec-badge {{
    font-size: 10px; font-weight: 700; padding: 2px 8px;
    border-radius: 6px; letter-spacing: 0.3px;
  }}
  .rec-buy  {{ background: var(--teal-light); color: var(--teal-dark); }}
  .rec-hold {{ background: var(--gold-light);  color: var(--gold); }}
  .rec-sell {{ background: {pal["danger_bg"]}; color: var(--red); }}
  .rec-none {{ background: {pal["empty_bg"]}; color: var(--text-muted); }}

  .card-row2 {{ display: flex; align-items: center; justify-content: space-between; gap: 6px; }}
  .card-reason {{
    font-size: 12px; color: var(--text-sub); flex: 1;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    padding-right: 6px;
  }}
  .change-badge {{ font-size: 13px; font-weight: 700; flex-shrink: 0; }}
  .change-badge.up      {{ color: var(--up); }}
  .change-badge.down    {{ color: var(--down); }}
  .change-badge.neutral {{ color: var(--text-muted); }}

  .card-chips {{ display: flex; gap: 5px; margin-top: 6px; flex-wrap: wrap; }}
  .chip {{
    font-size: 10px; padding: 2px 8px; border-radius: 6px;
    background: {pal["empty_bg"]}; color: var(--text-sub);
    border: 0.5px solid var(--border);
  }}
  .chip.warn {{ background: rgba(224,82,82,0.07); color: #b04040; border-color: rgba(224,82,82,0.2); }}
  .chip.good {{ background: var(--teal-light); color: var(--teal-dark); border-color: rgba(15,110,86,0.2); }}
  .card-arrow {{ color: var(--text-muted); font-size: 15px; flex-shrink: 0; transition: transform 0.18s; }}
  .card-arrow.open {{ transform: rotate(90deg); }}

  /* ── 상세 패널 ── */
  .detail-wrap {{
    background: var(--surface2);
    border: 0.5px solid var(--teal);
    border-top: none;
    border-radius: 0 0 14px 14px;
    overflow: hidden;
  }}
  .dtab-header {{ display: flex; border-bottom: 0.5px solid var(--border); background: var(--surface); }}
  .dtab-btn {{
    flex: 1; padding: 9px 0; font-size: 12px; font-weight: 600;
    text-align: center; cursor: pointer; color: var(--text-muted);
    background: transparent; border: none; font-family: inherit;
    border-bottom: 2px solid transparent; margin-bottom: -1px;
    transition: all 0.15s;
  }}
  .dtab-btn.active {{ color: var(--teal); border-bottom-color: var(--teal); }}

  .dtab-content {{ display: none; padding: 13px 15px; }}
  .dtab-content.active {{ display: block; }}

  /* AI 탭 */
  .ai-summary {{
    background: var(--teal-light); border: 0.5px solid rgba(15,110,86,0.2);
    border-radius: 10px; padding: 10px 13px; margin-bottom: 11px;
    font-size: 12.5px; color: var(--text); line-height: 1.55; font-weight: 500;
  }}
  .bullet-list {{ display: flex; flex-direction: column; gap: 7px; margin-bottom: 10px; }}
  .bullet-item {{ display: flex; gap: 7px; align-items: flex-start; font-size: 12px; color: var(--text-sub); line-height: 1.5; }}
  .bullet-label {{
    font-size: 9.5px; font-weight: 700; padding: 2px 6px; border-radius: 4px; flex-shrink: 0; margin-top: 1px;
  }}
  .bl-q  {{ background: rgba(15,110,86,0.12); color: var(--teal-dark); }}
  .bl-n  {{ background: rgba(15,110,86,0.08); color: var(--teal); }}
  .bl-a  {{ background: var(--gold-light); color: var(--gold); }}
  .bl-ai {{ background: rgba(74,144,217,0.1); color: #2a70b0; }}
  .tag-row {{ display: flex; flex-wrap: wrap; gap: 5px; margin-top: 8px; }}
  .tag {{
    padding: 2px 8px; background: rgba(15,110,86,0.06);
    border: 0.5px solid var(--border); border-radius: 6px;
    font-size: 10.5px; color: var(--text-sub);
  }}

  /* 연관기업 */
  .related-section {{ border-top: 0.5px solid var(--border); padding-top: 10px; margin-top: 8px; }}
  .related-title {{ font-size: 11px; font-weight: 600; color: var(--text-sub); margin-bottom: 6px; }}
  .related-item {{
    background: var(--surface); border: 0.5px solid var(--border);
    border-radius: 9px; padding: 8px 11px; margin-bottom: 5px;
  }}
  .related-ticker {{ font-size: 12.5px; font-weight: 700; color: var(--teal); }}
  .related-reason {{ font-size: 11px; color: var(--text-muted); margin-top: 2px; line-height: 1.4; }}

  /* 애널리스트 탭 */
  .ana-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 7px; margin-bottom: 9px; }}
  .ana-card {{
    background: var(--surface); border: 0.5px solid var(--border);
    border-radius: 10px; padding: 10px 12px;
  }}
  .ana-label {{ font-size: 10px; color: var(--text-muted); margin-bottom: 3px; font-weight: 600; letter-spacing: 0.3px; }}
  .ana-value {{ font-size: 15px; font-weight: 700; color: var(--text); }}
  .ana-sub   {{ font-size: 10.5px; color: var(--text-muted); margin-top: 2px; }}
  .ana-up    {{ color: var(--teal-dark); }}
  .ana-dn    {{ color: var(--red); }}
  .earning-bar {{
    background: var(--surface); border: 0.5px solid var(--border);
    border-radius: 10px; padding: 10px 13px;
    display: flex; align-items: center; justify-content: space-between;
  }}
  .earning-left  {{ font-size: 12px; color: var(--text-sub); }}
  .earning-right {{ font-size: 12.5px; font-weight: 700; }}
  .earning-soon  {{ color: var(--yellow); }}
  .earning-past  {{ color: var(--text-muted); }}

  /* 뉴스 탭 */
  .news-item {{ padding: 10px 0; border-bottom: 0.5px solid var(--border2); }}
  .news-item:last-child {{ border-bottom: none; }}
  .news-source {{ font-size: 10px; color: var(--teal); font-weight: 700; margin-bottom: 3px; }}
  .news-title  {{ font-size: 12px; color: var(--text-sub); line-height: 1.45; margin-bottom: 4px; }}
  .news-snippet {{ font-size: 11px; color: var(--text-muted); line-height: 1.4; }}
  .news-senti  {{ display: inline-block; font-size: 10px; padding: 1px 6px; border-radius: 4px; margin-top: 4px; }}
  .senti-pos {{ background: var(--teal-light); color: var(--teal-dark); }}
  .senti-neg {{ background: {pal["danger_bg"]}; color: var(--red); }}
  .senti-neu {{ background: {pal["empty_bg"]}; color: var(--text-muted); }}

  .no-signal {{ font-size: 12.5px; color: var(--text-muted); padding: 16px 0; text-align: center; }}
  .empty-state {{ text-align: center; padding: 36px 20px; color: var(--text-muted); }}
  .empty-state .icon {{ font-size: 34px; margin-bottom: 10px; }}
</style>
</head>
<body>

<div class="filter-bar">
  <button class="filter-btn active" onclick="setFilter(this,'all')">전체</button>
  <button class="filter-btn" onclick="setFilter(this,'up')">상승</button>
  <button class="filter-btn" onclick="setFilter(this,'down')">하락</button>
</div>

<div class="card-list" id="cardList"></div>

<script>
const RAW = {signals_json};

const COLORS = [
  "#1457A8","#4a90d9","#c9873a","#8b72c8","#5ab87a",
  "#e05252","#a0b4b2","#3a8fc8","#c96a8b","#6a9e4a","#b8922a","#4a70a9"
];
const BRAND = {{
  AAPL:"#555",MSFT:"#0078d4",NVDA:"#76b900",AMZN:"#ff9900",
  GOOGL:"#4285f4",META:"#1877f2",TSLA:"#cc0000",
}};
function tc(t,i){{ return BRAND[t]||COLORS[i%COLORS.length]; }}

function iconHtml(ticker,idx,logo){{
  const c = tc(ticker,idx);
  if(logo) return `<div class="ticker-icon" style="background:#f0f8f7">
    <img src="${{logo}}" width="36" height="36" style="object-fit:contain;padding:4px;border-radius:10px"
         onerror="this.parentElement.style.background='${{c}}1a';this.parentElement.innerHTML='${{ticker.slice(0,2)}}'">
  </div>`;
  return `<div class="ticker-icon" style="background:${{c}}1a;color:${{c}}">${{ticker.slice(0,2)}}</div>`;
}}

let curFilter = "all";
let openTicker = null;
let openTab = {{}};

function setFilter(el,f){{
  document.querySelectorAll(".filter-btn").forEach(b=>b.classList.remove("active"));
  el.classList.add("active");
  curFilter=f; render();
}}
function toggleCard(t){{
  openTicker = openTicker===t ? null : t;
  if(openTicker && !openTab[t]) openTab[t]="ai";
  render();
}}
function switchTab(t,tab,e){{ e.stopPropagation(); openTab[t]=tab; render(); }}

function sigClass(item){{
  const c=item?.change_pct;
  if(c==null) return "neutral";
  return c>0?"up":c<0?"down":"neutral";
}}
function recHtml(rec){{
  if(!rec) return `<span class="rec-badge rec-none">N/A</span>`;
  const r=rec.toUpperCase();
  const cls=r.includes("BUY")?"rec-buy":r.includes("SELL")?"rec-sell":"rec-hold";
  const lbl=r==="STRONG_BUY"?"강력매수":r==="BUY"?"매수":r==="HOLD"?"보유":r==="SELL"?"매도":"매도";
  return `<span class="rec-badge ${{cls}}">${{lbl}}</span>`;
}}
function earningChip(ana){{
  if(ana?.earnings_days_left==null) return "";
  const d=ana.earnings_days_left;
  if(d<0) return `<span class="chip">어닝 D+${{Math.abs(d)}} 완료</span>`;
  if(d<=7) return `<span class="chip warn">🔔 어닝 D-${{d}}</span>`;
  if(d<=30) return `<span class="chip">어닝 D-${{d}}</span>`;
  return "";
}}
function upsideChip(ana){{
  if(ana?.target_upside_pct==null) return "";
  const u=ana.target_upside_pct;
  const cls=u>15?"good":u<-5?"warn":"";
  return `<span class="chip ${{cls}}">목표가 ${{u>0?"+":""}}${{u.toFixed(1)}}%</span>`;
}}

function render(){{
  const list=document.getElementById("cardList");
  let data=[...RAW];
  if(curFilter==="up")      data=data.filter(d=>sigClass(d)==="up");
  if(curFilter==="down")    data=data.filter(d=>sigClass(d)==="down");
  if(!data.length){{
    list.innerHTML=`<div class="empty-state"><div class="icon">🔍</div><p>해당하는 시그널이 없어요</p></div>`;
    return;
  }}
  list.innerHTML=data.map((item,i)=>{{
    const t=item.ticker;
    const sc=sigClass(item);
    const chg=item.change_pct!=null?parseFloat(item.change_pct):null;
    const chgStr=chg!=null?(chg>=0?"+":"")+chg.toFixed(2)+"%":"N/A";
    const arrow=sc==="up"?"▲":sc==="down"?"▼":"—";
    const reason=item.signal?.reason||"분석 정보 없음";
    const isOpen=openTicker===t;
    const ana=item.analyst||{{}};
    const detail=isOpen?renderDetail(item,i):"";
    return `
    <div>
      <div class="signal-card ${{sc}}${{isOpen?" open":""}}" onclick="toggleCard('${{t}}')">
        <div class="card-head">
          ${{iconHtml(t,i,item.logo_url)}}
          <div class="card-body">
            <div class="card-row1">
              <span class="ticker-name">${{t}}<span class="ticker-shares">${{item.shares?.toFixed(2)}}주</span></span>
              <div style="display:flex;align-items:center;gap:5px">
                ${{recHtml(ana.rec_key)}}
              </div>
            </div>
            <div class="card-row2">
              <span class="card-reason">${{reason}}</span>
              <span class="change-badge ${{sc}}">${{arrow}} ${{chgStr}}</span>
            </div>
            <div class="card-chips">
              ${{earningChip(ana)}}${{upsideChip(ana)}}
            </div>
          </div>
          <div class="card-arrow${{isOpen?" open":""}}">›</div>
        </div>
      </div>
      ${{detail}}
    </div>`;
  }}).join("");
}}

function renderDetail(item,idx){{
  if(!item.signal||item.signal._error){{
    const msg=item.signal?._error||"AI 분석 실패";
    return `<div class="detail-wrap"><div class="no-signal">⚠️ ${{msg}}</div></div>`;
  }}
  const t=item.ticker, tab=openTab[t]||"ai";
  const sig=item.signal, ana=item.analyst||{{}}, arts=item.articles||[];

  const tabs=[{{id:"ai",label:"💡 AI 의견"}},{{id:"analyst",label:"📊 애널리스트"}},{{id:"news",label:"📰 뉴스"}}];
  const header=`<div class="dtab-header">
    ${{tabs.map(tb=>`<button class="dtab-btn${{tab===tb.id?" active":""}}" onclick="switchTab('${{t}}','${{tb.id}}',event)">${{tb.label}}</button>`).join("")}}
  </div>`;

  // AI 탭
  const bullets=sig.bullets||[];
  const lbls=["뉴스","애널리스트","액션","AI"];
  const lcls=["bl-q","bl-n","bl-a","bl-ai"];
  const bullHtml=bullets.map((b,i)=>`
    <div class="bullet-item">
      <span class="bullet-label ${{lcls[i]||"bl-q"}}">${{lbls[i]||""}}</span>
      <span>${{b}}</span>
    </div>`).join("");
  const tagsHtml=(sig.tags||[]).map(tg=>`<div class="tag">${{tg}}</div>`).join("");
  const related=sig.related||[];
  const relHtml=related.length?`<div class="related-section">
    <div class="related-title">🔗 연관 기업</div>
    ${{related.map(r=>`<div class="related-item"><div class="related-ticker">${{r.ticker}}</div><div class="related-reason">${{r.reason}}</div></div>`).join("")}}
  </div>`:"";
  const aiContent=`<div class="dtab-content${{tab==="ai"?" active":""}}">
    <div class="ai-summary">${{sig.reason||""}}</div>
    <div class="bullet-list">${{bullHtml}}</div>
    <div class="tag-row">${{tagsHtml}}</div>
    ${{relHtml}}
  </div>`;

  // 애널리스트 탭
  const recLbl=ana.rec_key?({{STRONG_BUY:"강력 매수",BUY:"매수",HOLD:"보유",SELL:"매도",UNDERPERFORM:"매도"}}[ana.rec_key]||ana.rec_key):"—";
  const nAna=ana.n_analysts?`${{ana.n_analysts}}명`:"";
  const recCls=ana.rec_key?.includes("BUY")?"ana-up":ana.rec_key?.includes("SELL")?"ana-dn":"";
  const up=ana.target_upside_pct;
  const upCls=up!=null?(up>0?"ana-up":"ana-dn"):"";
  const upStr=up!=null?`${{up>0?"+":""}}${{up.toFixed(1)}}%`:"—";
  const eps=ana.eps_surprise_pct;
  const epsCls=eps!=null?(eps>0?"ana-up":"ana-dn"):"";
  const epsStr=eps!=null?`${{eps>0?"+":""}}${{eps.toFixed(1)}}%`:"—";
  let earHtml="";
  if(ana.earnings_date){{
    const d=ana.earnings_days_left;
    const lbl=d<0?`D+${{Math.abs(d)}} 완료`:`D-${{d}} 예정`;
    const cls=d!=null&&d<=14&&d>=0?"earning-soon":"earning-past";
    earHtml=`<div class="earning-bar">
      <span class="earning-left">📅 실적 발표</span>
      <span class="earning-right ${{cls}}">${{ana.earnings_date}} · ${{lbl}}</span>
    </div>`;
  }}
  const analystContent=`<div class="dtab-content${{tab==="analyst"?" active":""}}">
    <div class="ana-grid">
      <div class="ana-card"><div class="ana-label">투자의견</div><div class="ana-value ${{recCls}}">${{recLbl}}</div><div class="ana-sub">${{nAna}} 애널리스트</div></div>
      <div class="ana-card"><div class="ana-label">목표가 상승여력</div><div class="ana-value ${{upCls}}">${{upStr}}</div><div class="ana-sub">${{ana.target_mean?"$"+ana.target_mean:"—"}}</div></div>
      <div class="ana-card"><div class="ana-label">EPS 서프라이즈</div><div class="ana-value ${{epsCls}}">${{epsStr}}</div><div class="ana-sub">전분기 대비</div></div>
      <div class="ana-card"><div class="ana-label">목표가 범위</div><div class="ana-value" style="font-size:12px">${{ana.target_low?"$"+ana.target_low:"—"}} ~ ${{ana.target_high?"$"+ana.target_high:"—"}}</div><div class="ana-sub">Low ~ High</div></div>
    </div>
    ${{earHtml}}
  </div>`;

  // 뉴스 탭
  const newsHtml=arts.length?arts.slice(0,5).map(a=>{{
    const s=a.sentiment;
    const scls=s!=null?(s>0.2?"senti-pos":s<-0.2?"senti-neg":"senti-neu"):"";
    const slbl=s!=null?(s>0.2?"긍정":s<-0.2?"부정":"중립"):"";
    return `<div class="news-item">
      ${{a.source?`<div class="news-source">${{a.source}}</div>`:""}}
      <div class="news-title">${{a.title||""}}</div>
      ${{a.snippet?`<div class="news-snippet">${{a.snippet.slice(0,150)}}</div>`:""}}
      ${{scls?`<span class="news-senti ${{scls}}">${{slbl}}</span>`:""}}
    </div>`;
  }}).join(""):
  `<div class="no-signal">최근 뉴스 없음</div>`;
  const newsContent=`<div class="dtab-content${{tab==="news"?" active":""}}">
    ${{newsHtml}}
  </div>`;

  return `<div class="detail-wrap">
    ${{header}}${{aiContent}}${{analystContent}}${{newsContent}}
  </div>`;
}}

render();
</script>
</body>
</html>
"""
    n = len(signals)
    height = max(500, n * 110 + 200)
    st.components.v1.html(html, height=height, scrolling=True)
