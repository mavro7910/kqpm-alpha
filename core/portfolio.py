"""
core/portfolio.py

로컬 실행  → data/portfolio_{uid}.json 파일 저장 (기존 방식 유지)
Cloud 실행 → Supabase DB 저장/로드 (영구 보존)

환경 판단: st.secrets에 SUPABASE_URL이 있으면 Cloud 모드
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from core.data import is_krx_ticker, looks_broken_korean, normalize_kr_ticker


# ─────────────────────────────────────────────
# Supabase 클라이언트 (Cloud 환경에서만 초기화)
# ─────────────────────────────────────────────

def _get_supabase():
    """Supabase 클라이언트 반환. secrets 없으면 None."""
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
        from supabase import create_client
        return create_client(url, key)
    except Exception:
        return None


# ─────────────────────────────────────────────
# Portfolio 클래스
# ─────────────────────────────────────────────

_DEFAULTS = {
    "holdings": {
        "005930": 0,
        "000660": 0,
        "035420": 0,
        "005380": 0,
        "069500": 0,
        "229200": 0,
        "305720": 0,
    },
    "weekly_budget": 100_000,
    "benchmarks": ["069500", "229200"],
    "settings": {
        "top_n": 10,
        "ai_provider": "gemini",
        "signal_lang": "ko",
        # 탭별 마지막 설정 (각 탭에서 자동 저장)
        "buy_use_mcap": True,
        "bt_period": "3년",
        "sell_top_n": 15,
        "sell_use_mcap": True,
        "rebal_top_n": 15,
        "rebal_use_mcap": True,
        "auto_refresh_prices": False,
    },
}

SCHEMA_VERSION = 2
DATA_SOURCE = "naver_finance"
MARKET = "KRX"


class Portfolio:
    def __init__(self, path: Path):
        self.path = path
        self._supabase = _get_supabase()
        self._uid = path.stem.replace("portfolio_", "")  # 파일명에서 uid 추출
        self._data: dict = {}
        self._load()
        if self._normalize_data():
            self.save()

    # ── 내부 로드/저장 ────────────────────────────────────────────

    def _load(self):
        """Supabase 우선, 없으면 로컬 파일에서 로드."""
        if self._supabase:
            self._data = self._load_supabase()
        else:
            self._data = self._load_local()

    def _load_supabase(self) -> dict:
        try:
            res = (
                self._supabase.table("portfolios")
                .select("data")
                .eq("uid", self._uid)
                .execute()
            )
            if res.data:
                return res.data[0]["data"]
        except Exception as e:
            st.warning(f"Supabase 로드 실패, 기본값 사용: {e}")
        return dict(_DEFAULTS)

    def _load_local(self) -> dict:
        if self.path.exists():
            try:
                with open(self.path, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return dict(_DEFAULTS)

    def _normalize_data(self) -> bool:
        """Bring older local/Supabase blobs in line with the KRX/Naver schema."""
        before = json.dumps(self._data, ensure_ascii=False, sort_keys=True, default=str)
        if not isinstance(self._data, dict):
            self._data = {}

        raw_holdings = self._data.get("holdings", {})
        if not isinstance(raw_holdings, dict):
            raw_holdings = {}
        holdings = {}
        for ticker, shares in raw_holdings.items():
            symbol = normalize_kr_ticker(ticker)
            if not is_krx_ticker(symbol):
                continue
            try:
                holdings[symbol] = float(shares)
            except (TypeError, ValueError):
                holdings[symbol] = 0.0
        self._data["holdings"] = holdings or dict(_DEFAULTS["holdings"])

        symbols = set(self._data["holdings"])

        raw_benchmarks = self._data.get("benchmarks", _DEFAULTS["benchmarks"])
        if not isinstance(raw_benchmarks, list):
            raw_benchmarks = _DEFAULTS["benchmarks"]
        benchmarks = []
        for ticker in raw_benchmarks:
            symbol = normalize_kr_ticker(ticker)
            if is_krx_ticker(symbol) and symbol not in benchmarks:
                benchmarks.append(symbol)
        self._data["benchmarks"] = benchmarks or list(_DEFAULTS["benchmarks"])
        symbols.update(self._data["benchmarks"])

        settings = self._data.get("settings", {})
        if not isinstance(settings, dict):
            settings = {}
        merged_settings = dict(_DEFAULTS["settings"])
        merged_settings.update(settings)
        self._data["settings"] = merged_settings

        raw_names = self._data.get("names", {})
        if not isinstance(raw_names, dict):
            raw_names = {}
        names = {}
        for ticker, name in raw_names.items():
            symbol = normalize_kr_ticker(ticker)
            if symbol in symbols and name and not looks_broken_korean(str(name)):
                names[symbol] = str(name).strip()
        self._data["names"] = names

        raw_logos = self._data.get("logos", {})
        if not isinstance(raw_logos, dict):
            raw_logos = {}
        logos = {}
        for ticker, url in raw_logos.items():
            symbol = normalize_kr_ticker(ticker)
            if symbol in symbols and isinstance(url, str) and url.startswith(("http://", "https://")):
                logos[symbol] = url
        self._data["logos"] = logos

        self._data["schema_version"] = SCHEMA_VERSION
        self._data["data_source"] = DATA_SOURCE
        self._data["market"] = MARKET

        after = json.dumps(self._data, ensure_ascii=False, sort_keys=True, default=str)
        return after != before

    def save(self):
        """Supabase 우선, 없으면 로컬 파일에 저장."""
        if self._supabase:
            self._save_supabase()
        else:
            self._save_local()

    def _save_supabase(self):
        try:
            self._supabase.table("portfolios").upsert(
                {"uid": self._uid, "data": self._data},
                on_conflict="uid",
            ).execute()
        except Exception as e:
            st.error(f"Supabase 저장 실패: {e}")

    def _save_local(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except OSError:
            # Streamlit Cloud read-only 파일시스템 폴백
            fallback = Path("/tmp") / self.path.name
            with open(fallback, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)

    # ── 공개 프로퍼티 ──────────────────────────────────────────────

    @property
    def holdings(self) -> dict:
        return self._data.setdefault("holdings", {})

    @property
    def weekly_budget(self) -> int:
        return self._data.get("weekly_budget", _DEFAULTS["weekly_budget"])

    @weekly_budget.setter
    def weekly_budget(self, value: int):
        self._data["weekly_budget"] = int(value)

    @property
    def benchmarks(self) -> list:
        return self._data.get("benchmarks", _DEFAULTS["benchmarks"])

    @benchmarks.setter
    def benchmarks(self, value: list):
        cleaned = []
        for ticker in value:
            symbol = normalize_kr_ticker(ticker)
            if is_krx_ticker(symbol) and symbol not in cleaned:
                cleaned.append(symbol)
        self._data["benchmarks"] = cleaned or list(_DEFAULTS["benchmarks"])

    def tickers(self) -> list:
        return list(self.holdings.keys())

    def set_holding(self, ticker: str, shares: float):
        symbol = normalize_kr_ticker(ticker)
        if not is_krx_ticker(symbol):
            raise ValueError("국내 종목/ETF 6자리 티커를 입력하세요.")
        self._data.setdefault("holdings", {})[symbol] = shares

    def remove_holding(self, ticker: str):
        symbol = normalize_kr_ticker(ticker)
        self._data.setdefault("holdings", {}).pop(symbol, None)
        self._data.setdefault("names", {}).pop(symbol, None)
        self._data.setdefault("logos", {}).pop(symbol, None)

    # ── 설정값 ────────────────────────────────────────────────────

    @property
    def settings(self) -> dict:
        return self._data.setdefault("settings", dict(_DEFAULTS["settings"]))

    def get_setting(self, key: str, default=None):
        return self.settings.get(key, _DEFAULTS["settings"].get(key, default))

    def set_setting(self, key: str, value):
        self.settings[key] = value

    # ── 로고 캐시 ──────────────────────────────────────────────────

    @property
    def logos(self) -> dict:
        return self._data.setdefault("logos", {})

    def get_logo(self, ticker: str) -> str | None:
        return self.logos.get(normalize_kr_ticker(ticker))

    def set_logo(self, ticker: str, url: str | None):
        if url:
            self.logos[normalize_kr_ticker(ticker)] = url

    # ── 종목명 캐시 ──────────────────────────────────────────────────

    @property
    def names(self) -> dict:
        return self._data.setdefault("names", {})

    def get_name(self, ticker: str) -> str | None:
        symbol = normalize_kr_ticker(ticker)
        name = self.names.get(symbol)
        if looks_broken_korean(name):
            self.names.pop(symbol, None)
            return None
        return name

    def set_name(self, ticker: str, name: str | None):
        if name and not looks_broken_korean(name):
            self.names[normalize_kr_ticker(ticker)] = name

    def replace_data(self, data: dict):
        self._data = data if isinstance(data, dict) else {}
        self._normalize_data()
