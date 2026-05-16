"""
utils/ai_client.py

AI 시그널 분석 — Naver Open API 뉴스 + Naver Finance 시세 기반.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import streamlit as st


# ─────────────────────────────────────────────
# API 키 관리
# ─────────────────────────────────────────────

def get_api_key() -> str | None:
    return st.session_state.get("gemini_api_key") or None

def set_api_key(key: str):
    st.session_state["gemini_api_key"] = key.strip()

def clear_api_key():
    st.session_state.pop("gemini_api_key", None)

def has_api_key() -> bool:
    k = get_api_key()
    return bool(k and len(k) > 10)

def get_naver_client_id() -> str | None:
    return st.session_state.get("naver_client_id") or None

def set_naver_client_id(key: str):
    st.session_state["naver_client_id"] = key.strip()

def get_naver_client_secret() -> str | None:
    return st.session_state.get("naver_client_secret") or None

def set_naver_client_secret(key: str):
    st.session_state["naver_client_secret"] = key.strip()

def clear_naver_keys():
    st.session_state.pop("naver_client_id", None)
    st.session_state.pop("naver_client_secret", None)

def has_naver_keys() -> bool:
    return bool(get_naver_client_id() and get_naver_client_secret())

def get_dart_key() -> str | None:
    return st.session_state.get("dart_api_key") or None

def set_dart_key(key: str):
    st.session_state["dart_api_key"] = key.strip()

def clear_dart_key():
    st.session_state.pop("dart_api_key", None)

def has_dart_key() -> bool:
    key = get_dart_key()
    return bool(key and len(key) >= 20)

# ─────────────────────────────────────────────
# 키 검증
# ─────────────────────────────────────────────

def validate_api_key(api_key: str) -> tuple[bool, str | None]:
    key = api_key.strip()
    if not key.startswith("AIza"):
        return False, "Gemini 키는 'AIza'로 시작해야 합니다."
    if len(key) < 35:
        return False, "키가 너무 짧습니다."
    return True, None

def validate_naver_keys(client_id: str, client_secret: str) -> tuple[bool, str | None]:
    if len(client_id.strip()) < 5:
        return False, "Naver Client ID가 너무 짧습니다."
    if len(client_secret.strip()) < 5:
        return False, "Naver Client Secret이 너무 짧습니다."
    return True, None

def validate_dart_key(api_key: str) -> tuple[bool, str | None]:
    if len(api_key.strip()) < 20:
        return False, "DART API 키가 너무 짧습니다."
    return True, None

def fetch_analyst_data(tickers: list[str]) -> dict[str, dict]:
    """
    투자의견/목표주가는 국내 종목에서 안정적인 공개 API가 제한적이므로 best-effort로 비워둡니다.
    현재가는 네이버 금융 시세로 보완합니다.
    """
    from core.data import fetch_last_close

    result = {}
    for t in tickers:
        price = fetch_last_close(t, period="10d")
        result[t] = {
            "rec_key": None,
            "rec_mean": None,
            "n_analysts": None,
            "current_price": round(float(price), 0) if price else None,
            "target_mean": None,
            "target_high": None,
            "target_low": None,
            "target_upside_pct": None,
            "earnings_date": None,
            "earnings_days_left": None,
            "eps_surprise_pct": None,
        }

    return result


# ─────────────────────────────────────────────
# 뉴스 수집
# ─────────────────────────────────────────────

def _strip_html(value: str) -> str:
    value = re.sub(r"<.*?>", " ", value or "")
    value = value.replace("&quot;", '"').replace("&amp;", "&")
    value = value.replace("&lt;", "<").replace("&gt;", ">").replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", value).strip()


def _fetch_naver_openapi_news(
    ticker: str,
    client_id: str | None,
    client_secret: str | None,
) -> list[dict]:
    if not client_id or not client_secret:
        return []

    import requests

    try:
        from core.data import fetch_name
        name = fetch_name(ticker) or ticker
        query = f"{name} {ticker} 주가"
        r = requests.get(
            "https://openapi.naver.com/v1/search/news.json",
            params={"query": query, "display": 5, "sort": "date"},
            headers={
                "X-Naver-Client-Id": client_id,
                "X-Naver-Client-Secret": client_secret,
            },
            timeout=8,
        )
        if r.status_code != 200:
            return []
        articles = []
        for item in r.json().get("items", [])[:5]:
            title = _strip_html(item.get("title", ""))
            desc = _strip_html(item.get("description", ""))
            if title:
                articles.append({
                    "title": title,
                    "snippet": desc[:400],
                    "highlights": [],
                    "source": "Naver News",
                    "url": item.get("originallink") or item.get("link"),
                    "sentiment": None,
                })
        return articles
    except Exception:
        return []


def _fetch_naver_fallback(
    ticker: str,
    naver_client_id: str | None = None,
    naver_client_secret: str | None = None,
) -> tuple[list[dict], float | None]:
    try:
        from core.data import fetch_ohlcv
        articles = _fetch_naver_openapi_news(ticker, naver_client_id, naver_client_secret)
        change_pct = None
        hist = fetch_ohlcv(ticker, period="10d")
        if len(hist) >= 2:
            prev = float(hist["Close"].iloc[-2])
            curr = float(hist["Close"].iloc[-1])
            if prev > 0:
                change_pct = round((curr - prev) / prev * 100, 2)

        return articles, change_pct
    except Exception:
        return [], None


def fetch_ticker_data(
    ticker: str,
    naver_client_id: str | None = None,
    naver_client_secret: str | None = None,
) -> tuple[list[dict], float | None, dict]:
    """
    Naver Open API 뉴스 + Naver Finance 시세 수집.

    Returns:
        (articles, change_pct, api_status)
        api_status: {
            "naver":     "ok" | "skip" | "fail" | "no_data",
        }
    """
    api_status: dict[str, str] = {
        "naver":     "pending",
    }
    try:
        articles, change_pct = _fetch_naver_fallback(ticker, naver_client_id, naver_client_secret)
        api_status["naver"] = "ok" if articles or change_pct is not None else "no_data"
        return articles, change_pct, api_status
    except Exception:
        api_status["naver"] = "fail"
        return [], None, api_status


# ─────────────────────────────────────────────
# 프롬프트 빌더
# ─────────────────────────────────────────────

_SYSTEM_PROMPT = """당신은 한국 주식 및 국내 상장 ETF 포트폴리오 분석 AI입니다.

[분석 기준]
- 네이버 뉴스와 네이버 Finance 가격 변화를 근거로 판단합니다
- 뉴스가 없으면 "최근 유의미한 뉴스 없음"이라고 명시하고, 뉴스 내용을 추측하거나 창작하지 마세요
- 투자의견·목표가·실적 일정은 제공된 경우에만 언급하세요
- 액션은 구체적 조건과 함께 제시하세요. "비중 유지" 단독 사용 금지
- JSON 배열만 응답하세요. 코드블록, 설명 텍스트 절대 없이

[signal 판정 — 반드시 준수]
- up: 명확한 호재 뉴스와 긍정적 가격 흐름이 함께 있을 때만
- down: 명확한 악재 뉴스와 부정적 가격 흐름이 함께 있을 때만
- neutral: 위 두 조건에 해당하지 않는 모든 경우 (뉴스 없음, 혼재, 한쪽만 있음, 단순 등락)
- 확신이 없으면 neutral. neutral이 가장 흔한 정상 상태임"""


def _format_news_block(articles: list[dict]) -> str:
    if not articles:
        return "없음"
    lines = []
    for art in articles[:3]:
        title      = art.get("title", "")
        snippet    = art.get("snippet", "")
        source     = art.get("source", "")
        highlights = art.get("highlights", [])
        senti      = art.get("sentiment")

        line = f"[{source}] {title}" if source else title
        if snippet:
            line += f"\n    본문: {snippet[:400]}"
        if highlights:
            line += f"\n    핵심구절: {' / '.join(highlights[:2])}"
        if senti is not None:
            label = "긍정" if senti > 0.2 else "부정" if senti < -0.2 else "중립"
            line += f"\n    감성: {label}({senti:+.2f})"
        lines.append(line)
    return "\n".join(lines)


def _analyst_conflict(ana: dict) -> str:
    """애널리스트 데이터 내 상충 신호 사전 감지."""
    signals = []
    rec = ana.get("rec_key", "")
    up  = ana.get("target_upside_pct")
    ed  = ana.get("earnings_days_left")
    eps = ana.get("eps_surprise_pct")

    if rec and "BUY" in rec and up is not None and up < -5:
        signals.append(f"⚠️ {rec} 의견이나 현재가가 목표주가를 {abs(up):.1f}% 상회 — 주가 선반영 또는 목표가 미업데이트 가능성")
    if ed is not None and 0 <= ed <= 7:
        signals.append(f"🔔 실적 발표 D-{ed} — 발표 전후 변동성 확대 구간, 포지션 주의")
    if eps is not None and eps < -10:
        signals.append(f"⚠️ 직전 EPS {eps:+.1f}% 미스 — 실적 신뢰도 하락, 이번 어닝 리스크 존재")
    if eps is not None and eps > 20 and ed is not None and ed > 0:
        signals.append(f"✅ 직전 EPS {eps:+.1f}% 서프라이즈 — 이번 어닝 기대감 유효")
    if rec and "SELL" in rec and up is not None and up > 5:
        signals.append(f"⚠️ 매도 의견이나 목표가 상승여력 {up:.1f}% — 애널리스트 간 의견 분화 가능성")

    return "\n  ".join(signals) if signals else "없음"


def _build_batch_prompt(
    holdings: dict,
    data_map: dict,
    analyst_ctx: dict,
) -> str:
    items = []
    for ticker, shares in holdings.items():
        articles, change_pct = data_map.get(ticker, ([], None))
        ana = analyst_ctx.get(ticker, {})

        change_str = f"{change_pct:+.2f}%" if change_pct is not None else "N/A"

        # 애널리스트 블록
        ana_parts = []
        if ana.get("rec_key"):
            n_str = f" ({ana['n_analysts']}명)" if ana.get("n_analysts") else ""
            ana_parts.append(f"투자의견:{ana['rec_key']}{n_str}")
        if ana.get("target_mean") and ana.get("current_price"):
            up = ana.get("target_upside_pct")
            up_str = f"({up:+.1f}%)" if up is not None else ""
            ana_parts.append(f"목표주가:${ana['target_mean']}{up_str} / 현재가:${ana['current_price']}")
        if ana.get("target_high") and ana.get("target_low"):
            ana_parts.append(f"목표가범위:${ana['target_low']}~${ana['target_high']}")
        if ana.get("earnings_days_left") is not None:
            d = ana["earnings_days_left"]
            label = f"D+{abs(d)}발표완료" if d < 0 else f"D-{d}발표예정"
            ana_parts.append(f"어닝:{label}({ana.get('earnings_date','')})")
        if ana.get("eps_surprise_pct") is not None:
            ana_parts.append(f"직전EPS서프라이즈:{ana['eps_surprise_pct']:+.1f}%")
        ana_str = "\n  ".join(ana_parts) if ana_parts else "없음"

        conflict  = _analyst_conflict(ana)
        news_str  = _format_news_block(articles)
        news_note = "" if articles else "\n  (뉴스 없음 — 뉴스 기반 언급 금지, 애널리스트 데이터만으로 판단)"

        items.append(
            f"[{ticker}] {shares:.1f}주 | 전일대비:{change_str}\n"
            f"  애널리스트:\n  {ana_str}\n"
            f"  사전감지신호:{conflict}\n"
            f"  뉴스:{news_note}\n  {news_str}"
        )

    tickers_list = list(holdings.keys())
    return (
        f"포트폴리오 {len(holdings)}개 종목 분석:\n\n"
        + "\n\n".join(items)
        + f"""

JSON 배열로만 응답 (코드블록 없이):
[{{"ticker":"종목","signal":"up/down/neutral","reason":"핵심판단40자이내","bullets":["뉴스해석","애널리스트해석(상충신호포함)","조건부액션","AI종합의견"],"tags":["태그1","태그2"],"related":[{{"ticker":"관련기업","reason":"연관이유"}}]}}]

rules:
- 한국어
- signal 판정 기준 (반드시 준수):
  up: 명확한 호재 뉴스 AND 애널리스트 긍정 신호가 동시에 존재할 때만
  down: 명확한 악재 뉴스 AND 애널리스트 부정 신호가 동시에 존재할 때만
  neutral: 그 외 모든 경우. 뉴스 없음/혼재/단순 등락/한쪽 신호만 있을 때
  → 확신 없으면 반드시 neutral. up/down은 명백한 근거 있을 때만 사용
- reason: 40자 이내, 가장 핵심적인 판단 한 문장
- bullets 정확히 4개:
  ①뉴스해석: 뉴스 본문 내용 기반 해석. 뉴스 없으면 "최근 유의미한 뉴스 없음"
  ②애널리스트: 투자의견·목표가·어닝·EPS 종합 해석. 사전감지신호 반드시 반영
  ③조건부액션: "~확인되면 비중확대 / ~시 일부 축소" 형식. 조건 없는 단순 유지 금지
  ④AI종합의견: 위 3개를 바탕으로 한 AI의 최종 판단. 확인된 데이터만 근거로 사용, 추측 금지
- related: 직접 연관된 실제 기업 1~2개 (공급사/경쟁사/파트너), 없으면[]
- 반드시 {len(holdings)}개 전부 포함: {', '.join(tickers_list)}"""
    )


# ─────────────────────────────────────────────
# Gemini 호출
# ─────────────────────────────────────────────

def _gemini_batch(
    holdings: dict,
    data_map: dict,
    analyst_ctx: dict,
    api_key: str,
) -> dict[str, dict]:
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash-lite",
        system_instruction=_SYSTEM_PROMPT,
    )
    response = model.generate_content(
        _build_batch_prompt(holdings, data_map, analyst_ctx)
    )
    raw = response.text.strip()
    raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()

    results_list = json.loads(raw)

    # Gemini가 중첩 리스트로 응답하는 경우 방어
    if isinstance(results_list, list) and results_list and isinstance(results_list[0], list):
        results_list = results_list[0]
    if isinstance(results_list, dict):
        results_list = list(results_list.values())

    result_map = {}
    for item in results_list:
        ticker = item.get("ticker", "")
        if not ticker:
            continue
        _, change_pct = data_map.get(ticker, ([], None))
        item.setdefault("signal", "neutral")
        item.setdefault("reason", "분석 정보 없음")
        item.setdefault("bullets", ["정보 없음"] * 4)
        item.setdefault("tags", [])
        item.setdefault("related", [])
        while len(item["bullets"]) < 4:
            item["bullets"].append("추가 정보 없음")
        item["bullets"] = item["bullets"][:4]
        result_map[ticker] = item

    return result_map


def _gemini_single(
    ticker: str,
    shares: float,
    articles: list[dict],
    change_pct: float | None,
    analyst_ctx: dict,
    api_key: str,
) -> dict:
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash-lite",
        system_instruction=_SYSTEM_PROMPT,
    )
    change_str = f"{change_pct:+.2f}%" if change_pct is not None else "N/A"
    news_str   = _format_news_block(articles)
    ana        = analyst_ctx.get(ticker, {})

    ana_parts = []
    if ana.get("rec_key"):
        ana_parts.append(f"투자의견:{ana['rec_key']}")
    if ana.get("target_upside_pct") is not None:
        ana_parts.append(f"목표가괴리:{ana['target_upside_pct']:+.1f}%")
    if ana.get("earnings_days_left") is not None:
        d = ana["earnings_days_left"]
        ana_parts.append(f"어닝:{'D+'+str(abs(d))+'완료' if d<0 else 'D-'+str(d)+'예정'}")
    if ana.get("eps_surprise_pct") is not None:
        ana_parts.append(f"직전EPS:{ana['eps_surprise_pct']:+.1f}%")
    ana_str  = " | ".join(ana_parts) if ana_parts else "없음"
    conflict = _analyst_conflict(ana)
    news_note = "" if articles else " (뉴스 없음 — 추측 금지)"

    prompt = (
        f"종목:{ticker} ({shares:.1f}주) | 전일대비:{change_str}\n"
        f"애널리스트: {ana_str}\n"
        f"사전감지신호: {conflict}\n"
        f"뉴스:{news_note}\n{news_str}\n\n"
        f'JSON:{{"signal":"up/down/neutral","reason":"40자이내","bullets":["뉴스해석","애널리스트해석(상충포함)","조건부액션","AI종합의견(확인된데이터만근거)"],"tags":["태그1"],"related":[]}}\n'
        "signal 기준: up=호재뉴스+애널리스트긍정 동시 / down=악재뉴스+애널리스트부정 동시 / neutral=그 외 모든 경우(확신없으면neutral)"
    )

    response = model.generate_content(prompt)
    raw = response.text.strip()
    raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    result = json.loads(raw)

    result.setdefault("signal", "neutral")
    result.setdefault("reason", "분석 정보 없음")
    result.setdefault("bullets", ["정보 없음"] * 4)
    result.setdefault("tags", [])
    result.setdefault("related", [])
    while len(result["bullets"]) < 4:
        result["bullets"].append("추가 정보 없음")
    result["bullets"] = result["bullets"][:4]
    return result


# ─────────────────────────────────────────────
# 스마트 캐시
# ─────────────────────────────────────────────

REANALYZE_THRESHOLD = 2.0  # 전일 대비 2% 이상 변동 시만 재분석 (잦은 up/down 방지)


def _needs_reanalysis(
    ticker: str,
    change_pct: float | None,
    cached_results: list[dict],
) -> bool:
    """캐시 미존재 또는 변동 2% 초과 시 재분석.
    캐시가 있어도 signal은 항상 재판정 (analyze_portfolio_signals에서 처리).
    """
    if change_pct is None:
        return True
    if abs(change_pct) >= REANALYZE_THRESHOLD:
        return True
    return ticker not in {r["ticker"] for r in cached_results}


# ─────────────────────────────────────────────
# 메인 분석
# ─────────────────────────────────────────────

def analyze_portfolio_signals(
    holdings: dict[str, float],
    api_key: str,
    naver_client_id: str | None = None,
    naver_client_secret: str | None = None,
    progress_callback=None,
    portfolio=None,
    cached_results: list[dict] | None = None,
) -> list[dict]:
    holdings = {t: s for t, s in holdings.items() if s > 0}
    tickers  = list(holdings.keys())
    total    = len(tickers)

    from datetime import datetime
    today    = date.today().isoformat()
    now_time = datetime.now().strftime("%H:%M")

    # ── 1단계: 뉴스+시세 병렬 수집 ─────────────────────────────
    if progress_callback:
        progress_callback(0, total, "데이터 수집 중", None)

    data_map:       dict[str, tuple[list[dict], float | None]] = {}
    api_status_map: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=min(10, total)) as executor:
        futures = {
            executor.submit(
                fetch_ticker_data,
                t,
                naver_client_id,
                naver_client_secret,
            ): t
            for t in tickers
        }
        for future in as_completed(futures):
            t = futures[future]
            try:
                articles, change_pct, api_status = future.result()
                data_map[t]       = (articles, change_pct)
                api_status_map[t] = api_status
            except Exception:
                data_map[t]       = ([], None)
                api_status_map[t] = {"naver": "fail"}

    # ── 2단계: 애널리스트 데이터 수집 ───────────────────────────
    if progress_callback:
        progress_callback(0, total, "애널리스트 데이터 수집 중", None)

    analyst_ctx = fetch_analyst_data(tickers)

    # ── 3단계: 스마트 캐시 분리 ─────────────────────────────────
    cached_map: dict[str, dict] = {}
    if cached_results:
        for r in cached_results:
            cached_map[r["ticker"]] = r

    reanalyze_tickers: list[str] = []
    keep_tickers:      list[str] = []

    for t in tickers:
        _, change_pct = data_map.get(t, ([], None))
        if _needs_reanalysis(t, change_pct, cached_results or []):
            reanalyze_tickers.append(t)
        else:
            keep_tickers.append(t)

    # ── 4단계: Gemini 배치 분석 ─────────────────────────────────
    signal_map: dict[str, dict] = {}

    # keep_tickers: 뉴스/데이터는 캐시 재사용, signal은 항상 Gemini 재판정
    # (캐시된 signal을 그대로 쓰면 프롬프트 개선이 반영되지 않음)
    all_reanalyze = list(reanalyze_tickers) + list(keep_tickers)

    if all_reanalyze:
        if progress_callback:
            progress_callback(1, total, "AI 분석 중", None)

        re_holdings = {t: holdings[t]     for t in all_reanalyze}
        re_data     = {t: data_map[t]     for t in all_reanalyze}
        re_analyst  = {t: analyst_ctx.get(t, {}) for t in all_reanalyze}

        try:
            batch_result = _gemini_batch(re_holdings, re_data, re_analyst, api_key)
            signal_map.update(batch_result)
        except Exception:
            if progress_callback:
                progress_callback(1, total, "배치 실패, 순차 분석 중...", None)
            for i, t in enumerate(all_reanalyze):
                articles, change_pct = re_data.get(t, ([], None))
                try:
                    signal_map[t] = _gemini_single(
                        t, holdings[t], articles, change_pct, re_analyst, api_key
                    )
                except Exception as e2:
                    signal_map[t] = {"_error": str(e2)}
                if progress_callback:
                    progress_callback(i + 1, len(all_reanalyze), t, None)

    # ── 5단계: 결과 조합 ─────────────────────────────────────────
    results = []
    for ticker in tickers:
        articles, change_pct = data_map.get(ticker, ([], None))
        headlines = [a["title"] for a in articles if a.get("title")]

        if ticker in keep_tickers and ticker in cached_map:
            old = cached_map[ticker].copy()
            old["change_pct"]   = change_pct
            old["reused_cache"] = True
            results.append(old)
        else:
            ana  = analyst_ctx.get(ticker, {})
            item = {
                "ticker":        ticker,
                "shares":        holdings[ticker],
                "change_pct":    change_pct,
                "headlines":     headlines,
                "articles":      articles,
                "signal":        signal_map.get(ticker, {"_error": "분석 결과 없음"}),
                "logo_url":      portfolio.get_logo(ticker) if portfolio else None,
                "analyzed_date": today,
                "analyzed_time": now_time,
                "reused_cache":  False,
                "api_status":    api_status_map.get(ticker, {}),
                "analyst": {
                    "rec_key":            ana.get("rec_key"),
                    "rec_mean":           ana.get("rec_mean"),
                    "n_analysts":         ana.get("n_analysts"),
                    "current_price":      ana.get("current_price"),
                    "target_mean":        ana.get("target_mean"),
                    "target_high":        ana.get("target_high"),
                    "target_low":         ana.get("target_low"),
                    "target_upside_pct":  ana.get("target_upside_pct"),
                    "earnings_date":      ana.get("earnings_date"),
                    "earnings_days_left": ana.get("earnings_days_left"),
                    "eps_surprise_pct":   ana.get("eps_surprise_pct"),
                },
            }
            results.append(item)

        if progress_callback:
            progress_callback(tickers.index(ticker) + 1, total, ticker, results[-1])

    return results
