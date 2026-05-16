"""
core/data.py

Korean market data layer for KQPM Alpha.

Naver does not expose this as a formal public stock API, but Naver Finance's
chart endpoint is widely used for Korean stock/ETF daily OHLCV data. This
module wraps that endpoint behind stable app-facing helpers.
"""

from __future__ import annotations

import ast
import re
from datetime import date, timedelta
from html import unescape

import pandas as pd
import requests

NAVER_CHART_URL = "https://api.finance.naver.com/siseJson.naver"
NAVER_ITEM_URL = "https://finance.naver.com/item/main.naver"
KRW_FX_RATE = 1.0
MARKET_BENCHMARK = "069500"  # KODEX 200

NAVER_COLUMNS = {
    "날짜": "Date",
    "시가": "Open",
    "고가": "High",
    "저가": "Low",
    "종가": "Close",
    "거래량": "Volume",
    "외국인소진율": "ForeignRatio",
}

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


def _response_text(res: requests.Response) -> str:
    """Decode Naver responses using the server-provided charset first."""
    if not res.encoding:
        res.encoding = res.apparent_encoding or "utf-8"
    return res.text


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
    cleaned = (text or "").strip()
    if not cleaned:
        return pd.DataFrame()

    # The endpoint returns a JavaScript-style list and sometimes leaves a
    # trailing comma before the closing bracket.
    cleaned = re.sub(r",\s*\]", "]", cleaned)
    try:
        rows = ast.literal_eval(cleaned)
    except (SyntaxError, ValueError) as exc:
        raise ValueError("네이버 금융 응답을 해석하지 못했습니다.") from exc

    if not rows or len(rows) < 2:
        return pd.DataFrame()

    header = [str(x).strip() for x in rows[0]]
    df = pd.DataFrame(rows[1:], columns=header)
    if "날짜" not in df.columns or "종가" not in df.columns:
        return pd.DataFrame()

    df = df.rename(columns=NAVER_COLUMNS)
    df["Date"] = pd.to_datetime(df["Date"], format="%Y%m%d", errors="coerce")
    for col in ("Open", "High", "Low", "Close", "Volume", "ForeignRatio"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df.dropna(subset=["Date", "Close"]).set_index("Date").sort_index()


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
        text = _response_text(res)
        match = re.search(
            r'<div class="wrap_company">.*?<h2[^>]*>\s*(?:<a[^>]*>)?\s*(.*?)\s*(?:</a>)?\s*</h2>',
            text,
            re.S,
        )
        if match:
            name = re.sub(r"<[^>]+>", "", match.group(1)).strip()
            return unescape(name) or None
    except Exception:
        return None
    return None


def fetch_market_cap(ticker: str) -> float | None:
    """Best-effort current market cap in KRW from Naver Finance HTML."""
    symbol = normalize_kr_ticker(ticker)
    try:
        res = _SESSION.get(NAVER_ITEM_URL, params={"code": symbol}, timeout=6)
        res.raise_for_status()
        text = re.sub(r"\s+", " ", _response_text(res))
        match = re.search(
            r"시가총액</em>\s*</th>\s*<td[^>]*>\s*<em[^>]*>([\d,]+)</em>\s*억원",
            text,
        )
        if not match:
            return None
        return float(match.group(1).replace(",", "")) * 100_000_000
    except Exception:
        return None


def extract_close(raw: pd.DataFrame) -> pd.DataFrame:
    """Compatibility helper: extract close prices from OHLCV data."""
    if raw is None or raw.empty:
        return pd.DataFrame()
    if "Close" in raw.columns:
        return raw[["Close"]]
    if "종가" in raw.columns:
        return raw[["종가"]].rename(columns={"종가": "Close"})
    return raw


def fetch_last_close(ticker: str, period: str = "10d") -> float | None:
    """Return the latest close in KRW for one Korean stock/ETF."""
    try:
        df = fetch_ohlcv(ticker, period=period)
        if df.empty:
            return None
        close = df["Close"].dropna()
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
                frames[ticker] = df["Close"].rename(ticker)
        except Exception as exc:
            errors.append(f"{ticker}: {exc}")

    if not frames:
        detail = "; ".join(errors[:3]) if errors else "데이터 없음"
        raise ValueError(f"유효한 국장 데이터가 없습니다. {detail}")
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
