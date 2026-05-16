"""
core/portfolio.py

로컬 실행  → data/portfolio_{uid}.json 파일 저장 (기존 방식 유지)
Cloud 실행 → Supabase DB 저장/로드 (영구 보존)

환경 판단: st.secrets에 SUPABASE_URL이 있으면 Cloud 모드
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import streamlit as st


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


class Portfolio:
    def __init__(self, path: Path):
        self.path = path
        self._supabase = _get_supabase()
        self._uid = path.stem.replace("portfolio_", "")  # 파일명에서 uid 추출
        self._data: dict = {}
        self._load()

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
        self._data["benchmarks"] = value

    def tickers(self) -> list:
        return list(self.holdings.keys())

    def set_holding(self, ticker: str, shares: float):
        self._data.setdefault("holdings", {})[ticker] = shares

    def remove_holding(self, ticker: str):
        self._data.setdefault("holdings", {}).pop(ticker, None)

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
        return self.logos.get(ticker)

    def set_logo(self, ticker: str, url: str | None):
        if url:
            self.logos[ticker] = url

    # ── 종목명 캐시 ──────────────────────────────────────────────────

    @property
    def names(self) -> dict:
        return self._data.setdefault("names", {})

    def get_name(self, ticker: str) -> str | None:
        return self.names.get(ticker)

    def set_name(self, ticker: str, name: str | None):
        if name:
            self.names[ticker] = name
