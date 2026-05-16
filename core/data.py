"""
core/data.py

Korean market data layer.

The app keeps the original public function names so the Streamlit workflow stays
the same, but prices now come from Naver Finance's Korean stock/ETF endpoints.
All prices are KRW and the returned FX rate is intentionally fixed at 1.0.
"""

from __future__ import annotations

import ast
import re
from datetime import date, timedelta

import pandas as pd
import requests

NAVER_CHART_URL = "https://api.finance.naver.com/siseJson.naver"
NAVER_ITEM_URL = "https://finance.naver.com/item/main.naver"
KRW_FX_RATE = 1.0
MARKET_BENCHMARK = "069500"  # KODEX 200

_SESSION = requests.Session()
_SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
        ),
        "Referer": "https://finance.naver.com/",
    }
)


def normalize_kr_ticker(ticker: str) -> str:
    """Return a six-digit Naver/KRX symbol from common user input forms."""
    raw = str(ticker or "").strip().upper()
    raw = raw.replace(".KS", "").replace(".KQ", "")
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 6:
        return digits
    if raw.isdigit() and 1 <= len(raw) <= 6:
        return raw.zfill(6)
    return raw


def _period_start(period: str) -> date:
    today = date.today()
    match = re.fullmatch(r"(\d+)([dmy])", str(period).strip().lower())
    if not match:
        return today - timedelta(days=365 * 3 + 30)
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "d":
        return today - timedelta(days=amount + 10)
    if unit == "m":
        return today - timedelta(days=amount * 31 + 10)
    return today - timedelta(days=amount * 365 + 30)


def _parse_naver_chart(text: str) -> pd.DataFrame:
    cleaned = re.sub(r"^\s*|\s*$", "", text or "")
    if not cleaned:
        return pd.DataFrame()
    cleaned = re.sub(r",\s*\]", "]", cleaned)
    try:
        rows = ast.literal_eval(cleaned)
    except Exception as exc:
        raise ValueError("네이버 금융 응답을 해석하지 못했습니다.") from exc

    if not rows or len(rows) < 2:
        return pd.DataFrame()

    header = [str(x).strip() for x in rows[0]]
    body = rows[1:]
    df = pd.DataFrame(body, columns=header)
    if "날짜" not in df.columns or "종가" not in df.columns:
        return pd.DataFrame()

    df["날짜"] = pd.to_datetime(df["날짜"], format="%Y%m%d", errors="coerce")
    for col in ("시가", "고가", "저가", "종가", "거래량"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["날짜", "종가"]).set_index("날짜").sort_index()
    return df


def fetch_ohlcv(
    ticker: str,
    period: str = "3y",
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """Fetch daily OHLCV for a Korean stock or ETF from Naver Finance."""
    symbol = normalize_kr_ticker(ticker)
    end_date = pd.Timestamp(end).date() if end else date.today()
    start_date = pd.Timestamp(start).date() if start else _period_start(period)
    params = {
        "symbol": symbol,
        "requestType": 1,
        "startTime": start_date.strftime("%Y%m%d"),
        "endTime": end_date.strftime("%Y%m%d"),
        "timeframe": "day",
    }
    try:
        res = _SESSION.get(NAVER_CHART_URL, params=params, timeout=10)
        res.raise_for_status()
    except requests.RequestException as exc:
        raise ValueError(
            f"네이버 금융 시세 조회 실패: {symbol}. 네트워크 또는 네이버 금융 접근 상태를 확인하세요."
        ) from exc
    return _parse_naver_chart(res.text)


def fetch_name(ticker: str) -> str | None:
    """Best-effort Korean instrument name from the Naver item page."""
    symbol = normalize_kr_ticker(ticker)
    try:
        res = _SESSION.get(NAVER_ITEM_URL, params={"code": symbol}, timeout=6)
        res.raise_for_status()
        res.encoding = "euc-kr"
        m = re.search(r'<div class="wrap_company">\s*<h2[^>]*>(.*?)</h2>', res.text, re.S)
        if m:
            return re.sub(r"<.*?>", "", m.group(1)).strip()
    except Exception:
        return None
    return None


def fetch_market_cap(ticker: str) -> float | None:
    """Best-effort current market cap in KRW from Naver Finance HTML."""
    symbol = normalize_kr_ticker(ticker)
    try:
        res = _SESSION.get(NAVER_ITEM_URL, params={"code": symbol}, timeout=6)
        res.raise_for_status()
        res.encoding = "euc-kr"
        text = re.sub(r"\s+", " ", res.text)
        m = re.search(r"시가총액</em>\s*</th>\s*<td[^>]*>\s*<em[^>]*>([\d,]+)</em>\s*억원", text)
        if not m:
            return None
        return float(m.group(1).replace(",", "")) * 100_000_000
    except Exception:
        return None


def extract_close(raw: pd.DataFrame) -> pd.DataFrame:
    """Compatibility helper: extract close prices from Naver OHLCV data."""
    if raw is None or raw.empty:
        return pd.DataFrame()
    if "종가" in raw.columns:
        return raw[["종가"]].rename(columns={"종가": "Close"})
    if "Close" in raw.columns:
        return raw[["Close"]]
    return raw


def fetch_last_close(ticker: str, period: str = "10d") -> float | None:
    """Return the latest close in KRW for one Korean stock/ETF."""
    try:
        df = fetch_ohlcv(ticker, period=period)
        if df.empty:
            return None
        close = df["종가"].dropna()
        return float(close.iloc[-1]) if not close.empty else None
    except Exception:
        return None


def fetch_close_matrix(
    tickers: list[str],
    period: str = "3y",
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """Fetch a close-price matrix with original ticker labels as columns."""
    frames: dict[str, pd.Series] = {}
    errors: list[str] = []
    for ticker in list(dict.fromkeys(tickers)):
        try:
            df = fetch_ohlcv(ticker, period=period, start=start, end=end)
            if not df.empty:
                frames[ticker] = df["종가"].rename(ticker)
        except Exception as exc:
            errors.append(f"{ticker}: {exc}")

    if not frames:
        detail = "; ".join(errors[:3]) if errors else "데이터 없음"
        raise ValueError(f"유효한 국장 티커가 없습니다. {detail}")
    return pd.concat(frames.values(), axis=1).sort_index()


def fetch_prices_and_fx(
    tickers: list[str],
    period: str = "10d",
) -> tuple[pd.Series, float, bool]:
    """
    Fetch latest KRW prices for holdings.

    Returns:
        prices: {ticker: latest close in KRW}
        fx_rate: 1.0 because Korean-market prices are already KRW
        fx_estimated: False
    """
    if not tickers:
        raise ValueError("티커 목록이 비어 있습니다.")

    close = fetch_close_matrix(tickers, period=period).ffill()
    prices = close.iloc[-1].reindex(tickers)
    return prices, KRW_FX_RATE, False
