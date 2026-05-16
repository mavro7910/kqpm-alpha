"""
core/dart.py

Thin OpenDART client for Korean corporate fundamentals.
"""

from __future__ import annotations

from functools import lru_cache
from zipfile import ZipFile
import io
import xml.etree.ElementTree as ET

import pandas as pd
import requests

CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
FINANCIALS_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"

REPORT_CODES = {
    "annual": "11011",
    "half": "11012",
    "q1": "11013",
    "q3": "11014",
}


@lru_cache(maxsize=2)
def fetch_corp_codes(api_key: str) -> pd.DataFrame:
    """Download and parse OpenDART's corp-code ZIP into a DataFrame."""
    res = requests.get(CORP_CODE_URL, params={"crtfc_key": api_key}, timeout=20)
    res.raise_for_status()

    with ZipFile(io.BytesIO(res.content)) as zf:
        xml_name = zf.namelist()[0]
        root = ET.fromstring(zf.read(xml_name))

    rows = []
    for item in root.findall("list"):
        rows.append({
            "corp_code": item.findtext("corp_code"),
            "corp_name": item.findtext("corp_name"),
            "stock_code": item.findtext("stock_code"),
            "modify_date": item.findtext("modify_date"),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["stock_code"] = df["stock_code"].fillna("").str.strip()
    return df


def find_corp_code(api_key: str, ticker: str) -> str | None:
    """Return OpenDART corp_code for a six-digit KRX ticker."""
    from core.data import normalize_kr_ticker

    symbol = normalize_kr_ticker(ticker)
    df = fetch_corp_codes(api_key)
    if df.empty:
        return None
    matched = df[df["stock_code"] == symbol]
    if matched.empty:
        return None
    return str(matched.iloc[0]["corp_code"])


def fetch_single_company_financials(
    api_key: str,
    ticker: str,
    year: int,
    report: str = "annual",
) -> pd.DataFrame:
    """Fetch single-company financial statements from OpenDART."""
    corp_code = find_corp_code(api_key, ticker)
    if not corp_code:
        raise ValueError(f"DART 고유번호를 찾지 못했습니다: {ticker}")

    reprt_code = REPORT_CODES.get(report, report)
    res = requests.get(
        FINANCIALS_URL,
        params={
            "crtfc_key": api_key,
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": reprt_code,
        },
        timeout=15,
    )
    res.raise_for_status()
    payload = res.json()
    if payload.get("status") != "000":
        raise ValueError(payload.get("message") or "OpenDART 재무제표 조회 실패")

    return pd.DataFrame(payload.get("list", []))
