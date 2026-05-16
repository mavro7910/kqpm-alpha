"""
core/secrets_store.py

- API 키들: AES-256-GCM 암호화 → user_secrets 테이블
  저장 형식: {"gemini": "AIza...", "finnhub": "d1abc..."}
- 시그널 캐시: signal_cache 테이블 (날짜별)
"""

from __future__ import annotations
import base64
import os
import json
from datetime import date
import streamlit as st


def _derive_key(uid: str) -> bytes:
    import hmac, hashlib
    secret = ""
    try:
        secret = st.secrets.get("ES", "")
    except Exception:
        secret = os.getenv("ES", "")
    if not secret:
        raise ValueError("st.secrets['ES'] 가 설정되지 않았습니다.")
    raw = hmac.new(secret.encode(), uid.encode(), hashlib.sha256).digest()  # type: ignore[attr-defined]
    return hashlib.sha256(raw).digest()


# hmac.new 가 없는 Python 버전 대응 패치
import hmac as _hmac_mod
if not hasattr(_hmac_mod, "new"):
    import hashlib as _hl
    _hmac_mod.new = lambda key, msg=None, digestmod=None: _hmac_mod.HMAC(key, msg, digestmod)  # type: ignore[attr-defined]


def _encrypt(plaintext: str, uid: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = _derive_key(uid)
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode(), None)
    return base64.b64encode(nonce + ct).decode()


def _decrypt(blob: str, uid: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = _derive_key(uid)
    raw = base64.b64decode(blob)
    nonce, ct = raw[:12], raw[12:]
    return AESGCM(key).decrypt(nonce, ct, None).decode()


def _get_supabase():
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
        from supabase import create_client
        return create_client(url, key)
    except KeyError as e:
        raise RuntimeError(f"st.secrets에 {e} 키가 없습니다.")
    except Exception as e:
        raise RuntimeError(f"Supabase 연결 실패: {e}")


# ─────────────────────────────────────────────
# 내부: 키 dict 로드/저장
# ─────────────────────────────────────────────

def _load_keys_dict(uid: str) -> tuple[dict, str | None]:
    try:
        sb = _get_supabase()
        res = sb.table("user_secrets").select("s").eq("uid", uid).execute()
        if res.data:
            decrypted = _decrypt(res.data[0]["s"], uid)
            return json.loads(decrypted), None
        return {}, None
    except Exception as e:
        return {}, str(e)


def _save_keys_dict(uid: str, keys: dict) -> tuple[bool, str | None]:
    try:
        sb = _get_supabase()
        encrypted = _encrypt(json.dumps(keys), uid)
        sb.table("user_secrets").upsert(
            {"uid": uid, "s": encrypted},
            on_conflict="uid",
        ).execute()
        return True, None
    except Exception as e:
        return False, str(e)


# ─────────────────────────────────────────────
# Gemini 키
# ─────────────────────────────────────────────

def load_gemini_key(uid: str) -> tuple[str | None, str | None]:
    keys, err = _load_keys_dict(uid)
    return keys.get("gemini"), err

def save_gemini_key(uid: str, key: str) -> tuple[bool, str | None]:
    keys, _ = _load_keys_dict(uid)
    keys["gemini"] = key
    return _save_keys_dict(uid, keys)

def delete_gemini_key(uid: str) -> tuple[bool, str | None]:
    keys, _ = _load_keys_dict(uid)
    keys.pop("gemini", None)
    return _save_keys_dict(uid, keys)


# ─────────────────────────────────────────────
# Finnhub 키
# ─────────────────────────────────────────────

def load_finnhub_key(uid: str) -> tuple[str | None, str | None]:
    keys, err = _load_keys_dict(uid)
    return keys.get("finnhub"), err

def save_finnhub_key(uid: str, key: str) -> tuple[bool, str | None]:
    keys, _ = _load_keys_dict(uid)
    keys["finnhub"] = key
    return _save_keys_dict(uid, keys)

def delete_finnhub_key(uid: str) -> tuple[bool, str | None]:
    keys, _ = _load_keys_dict(uid)
    keys.pop("finnhub", None)
    return _save_keys_dict(uid, keys)


# ─────────────────────────────────────────────
# 시그널 캐시
# ─────────────────────────────────────────────

def _kst_today() -> str:
    """UTC+9 기준 오늘 날짜 반환 (Supabase 저장 키와 일치시키기 위해)"""
    from datetime import datetime, timedelta
    return (datetime.utcnow() + timedelta(hours=9)).date().isoformat()


def load_signal_cache(uid: str) -> tuple[list | None, str | None]:
    try:
        sb = _get_supabase()
        today = _kst_today()
        res = (
            sb.table("signal_cache")
            .select("data")
            .eq("uid", uid)
            .eq("cache_date", today)
            .execute()
        )
        if res.data:
            return res.data[0]["data"], None
        return None, None
    except Exception as e:
        return None, str(e)


def save_signal_cache(uid: str, data: list) -> tuple[bool, str | None]:
    try:
        sb = _get_supabase()
        today = _kst_today()
        sb.table("signal_cache").upsert(
            {"uid": uid, "cache_date": today, "data": data},
            on_conflict="uid,cache_date",
        ).execute()
        return True, None
    except Exception as e:
        return False, str(e)


# ─────────────────────────────────────────────
# Marketaux 키
# ─────────────────────────────────────────────

def load_marketaux_key(uid: str) -> tuple[str | None, str | None]:
    keys, err = _load_keys_dict(uid)
    return keys.get("marketaux"), err

def save_marketaux_key(uid: str, key: str) -> tuple[bool, str | None]:
    keys, _ = _load_keys_dict(uid)
    keys["marketaux"] = key
    return _save_keys_dict(uid, keys)

def delete_marketaux_key(uid: str) -> tuple[bool, str | None]:
    keys, _ = _load_keys_dict(uid)
    keys.pop("marketaux", None)
    return _save_keys_dict(uid, keys)


# ─────────────────────────────────────────────
# 하위 호환 (기존 코드용)
# ─────────────────────────────────────────────

def load_api_key(uid: str) -> tuple[str | None, str | None]:
    return load_gemini_key(uid)

def save_api_key(uid: str, key: str) -> tuple[bool, str | None]:
    return save_gemini_key(uid, key)

def delete_api_key(uid: str) -> tuple[bool, str | None]:
    return delete_gemini_key(uid)
