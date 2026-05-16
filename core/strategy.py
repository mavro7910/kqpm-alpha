"""
core/strategy.py

[알고리즘 개선 이력]
── v3 (현재) ────────────────────────────────────────────────────────
1.  [REFACTOR] 팩터 정규화: rank(pct=True) → Z-score ±3σ winsorize
      · rank는 종목 간 격차를 균일 간격으로 압축 (1등/꼴등 차이 무시)
      · Z-score는 실제 수익률·변동성 격차를 반영 (AQR 실증 방식)
2.  [REFACTOR] 알파 결합: 가중 기하평균(곱) 제거 → Z-score 가중합으로 통일
      · Z-score 합산이 AQR 검증 방식이며 수학적으로 명확
      · 음수 Z-score 종목은 ReLU(clip≥0)로 자연 배제
3.  [KQPM] 데이터 소스 국장 전환
      · 네이버 금융 일봉 API 기반 국내 주식/ETF 가격 사용
      · 국장 소수점 거래 미지원 반영: 매수/백테스트/리밸런싱 수량 정수화
── v2 ───────────────────────────────────────────────────────────────
4.  [FIX] Look-ahead bias: 발행주수×과거주가로 시총 근사
5.  [FIX] CAGR → XIRR (적립식 수익률 현실화)
6.  [FIX] Invested: 실제 집행액 누적 (과대계상 방지)
7.  [FIX] 워밍업 버퍼 420 달력일 확보
8.  [FIX] 벤치마크 미래 가격 유입 차단 (슬라이스 후 ffill)
9.  [KQPM] KRW 가격 기준으로 환율 변환 제거
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import warnings
from scipy.optimize import brentq
from core.data import (
    KRW_FX_RATE,
    MARKET_BENCHMARK,
    fetch_close_matrix,
    fetch_last_close,
    fetch_market_cap,
)

warnings.filterwarnings("ignore")

MAX_WEIGHT       = 0.25
MOMENTUM_WEIGHTS = {21: 0.1, 63: 0.2, 126: 0.3, 252: 0.4}
VOL_WINDOW       = 60
MA_WINDOW        = 200
FX_FALLBACK      = KRW_FX_RATE

# ──────────────────────────────────────────────
# 데이터 수집
# ──────────────────────────────────────────────

def fetch_prices(
    tickers: list[str],
    extra: list[str] | None = None,
    period: str = "3y",
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """
    start/end를 지정하면 period는 무시됩니다.
    반환 close는 ffill 미적용 raw — 백테스트 루프에서 슬라이스 후 ffill할 것.
    """
    extra   = extra or []
    all_sym = list(dict.fromkeys(tickers + extra))
    close = fetch_close_matrix(all_sym, period=period, start=start, end=end)

    result = {}
    available = close.columns.tolist()

    valid_tickers = [t for t in tickers if t in available]
    if not valid_tickers:
        raise ValueError(f"유효한 티커가 없습니다. available: {available}")

    result["prices"] = close[valid_tickers].copy()
    for sym in extra:
        if sym in available:
            result[sym] = close[sym]

    return result


def fetch_market_caps(tickers: list[str]) -> "tuple[pd.Series, bool]":
    """
    현재 시가총액 조회 (매수추천 전용 — 백테스트에서 사용 금지).

    전략:
    1차) yf.download로 최근 주가 + Ticker.fast_info.shares로 시총 근사
         → .info 루프 방식보다 rate limit·세션 오류에 강함
    2차) fast_info도 실패 시 .info['marketCap'] 개별 조회 (fallback)

    Returns
    -------
    (Series, ok: bool)
        ok=False → 과반 이상 조회 실패. 호출부에서 경고 표시 필요.
        ok=True  → 과반 이상 정상 조회됨.
    """
    caps = {}
    for t in tickers:
        caps[t] = fetch_market_cap(t) or np.nan

    s = pd.Series(caps, dtype=float)
    n_valid = int(s.notna().sum())
    ok = n_valid >= max(1, len(tickers) // 2)

    min_val = s.dropna().min()
    s = s.fillna(min_val if pd.notna(min_val) and min_val > 0 else 1.0)
    return s, ok


def fetch_shares_outstanding(tickers: list[str]) -> pd.Series:
    """
    발행주수 1회 조회.
    백테스트에서 '과거 주가 × 발행주수'로 시총을 근사할 때 사용.
    """
    shares = {}
    for t in tickers:
        try:
            mcap = fetch_market_cap(t)
            last_close = fetch_last_close(t, period="10d")
            shares[t] = float(mcap) / float(last_close) if mcap and last_close and last_close > 0 else np.nan
        except Exception:
            shares[t] = np.nan

    sr  = pd.Series(shares, dtype=float)
    med = sr.dropna().median()
    sr  = sr.fillna(med if pd.notna(med) and med > 0 else 1.0)
    return sr


def _get_loc_safe(index: pd.DatetimeIndex, date) -> int | None:
    mask = index <= date
    if not mask.any():
        return None
    return int(np.where(mask)[0][-1])


# ──────────────────────────────────────────────
# XIRR
# ──────────────────────────────────────────────

def xirr(dates: list, cashflows: list[float]) -> float:
    if len(dates) != len(cashflows) or len(dates) < 2:
        return float("nan")

    t0   = dates[0]
    days = np.array([(d - t0).days for d in dates], dtype=float)

    def npv(rate: float) -> float:
        if rate <= -1.0:
            return float("inf")
        return float(np.sum(np.array(cashflows) / (1.0 + rate) ** (days / 365.25)))

    try:
        return brentq(npv, -0.9999, 1000.0, maxiter=1000)
    except (ValueError, RuntimeError):
        return float("nan")


def calc_xirr_from_backtest(
    df_bt: pd.DataFrame,
    weekly_budget: int,
    col: str = "QPM_Alpha",
) -> float:
    """
    XIRR 계산.

    Invested 컬럼의 주간 증분을 실제 투자 현금흐름으로 사용.
    (투자 불가 주가 있을 경우 fixed weekly_budget 대신 실제 집행액 반영)
    마지막 날 평가금액을 회수 현금흐름으로 처리.
    """
    dates    = list(df_bt.index)
    invested = df_bt["Invested"].values

    # 주간 실제 투자액 = Invested 증분
    weekly_invested = np.diff(invested, prepend=0.0)

    cfs = [-float(w) for w in weekly_invested]
    cfs[-1] += float(df_bt[col].iloc[-1])   # 마지막 날 평가금 회수
    return xirr(dates, cfs)


# ──────────────────────────────────────────────
# 팩터 계산 (Z-score 기반, AQR 방식)
# ──────────────────────────────────────────────

ZSCORE_WINSORIZE = 3.0   # 극단 이상치 제한 (AQR 기준 ±3σ)

# 시총 반영 강도 프리셋
# 실험 근거: γ에 따른 Sharpe 차이 없음 → 투자 성향 선택의 문제
# MDD는 γ가 높을수록 소폭 개선 (대형주 낮은 변동성 효과)
# 집중도는 γ=30%에서 -2.6%p 완화 (소형주 모멘텀 집중 억제)
MCAP_PRESETS = {
    "factor":   0.00,   # 순수 팩터: 시총 무시, 모멘텀/변동성만
    "balanced": 0.15,   # 균형 (기본값): 팩터 85% + 시총 15%
    "mcap":     0.30,   # 시총 편향: 팩터 70% + 시총 30%
}


def _zscore(s: pd.Series, winsorize: float = ZSCORE_WINSORIZE) -> pd.Series:
    """
    시리즈를 Z-score 정규화 후 winsorize.
    표준편차 0(모든 값 동일)이면 영벡터 반환.
    """
    sig = float(s.std())
    if sig < 1e-8:
        return pd.Series(0.0, index=s.index)
    z = (s - s.mean()) / sig
    return z.clip(-winsorize, winsorize)


def _mcap_zscore(mcap: pd.Series, columns: pd.Index) -> pd.Series:
    """
    시총을 raw Z-score로 정규화.

    log 변환을 사용하지 않는 이유:
    삼성전자와 중소형주의 큰 시총 격차를 log 처리하면
    오히려 격차가 더 커지는 역효과가 발생 (실험 확인).
    raw Z-score가 실제 시총 분포를 더 직관적으로 반영.
    """
    mc = mcap.reindex(columns).fillna(0)
    if mc.sum() < 1e-8:
        return pd.Series(0.0, index=columns)
    return _zscore(mc)


def momentum_score(df: pd.DataFrame) -> pd.Series:
    """
    복수 룩백 가중 모멘텀 Z-score.

    각 기간의 수익률을 독립적으로 Z-score 정규화한 뒤 가중 합산합니다.
    Z-score를 쓰는 이유: rank는 종목 간 실제 수익률 격차를 균일 간격으로
    압축하지만, Z-score는 격차를 그대로 반영합니다 (AQR 실증 방식).

    NaN 처리: 기간 데이터 부족 시 해당 기간 건너뜀.
    """
    score   = pd.Series(0.0, index=df.columns)
    total_w = 0.0
    for days, w in MOMENTUM_WEIGHTS.items():
        if len(df) <= days:
            continue
        ret = df.pct_change(days).iloc[-1].fillna(0)
        score   += w * _zscore(ret)
        total_w += w
    if total_w > 0:
        score /= total_w
    return score


def vol_inv_zscore(df: pd.DataFrame, window: int = VOL_WINDOW) -> pd.Series:
    """
    변동성 역수 Z-score (낮은 변동성 → 높은 점수).

    60일 일간 수익률 표준편차의 역수를 Z-score 정규화합니다.
    NaN/0 변동성 → 최대 변동성으로 대체 (가장 낮은 역수 점수 부여).
    """
    vol = df.pct_change().rolling(window).std().iloc[-1]
    vol = vol.replace(0, np.nan)
    max_vol = vol.dropna().max()
    vol = vol.fillna(max_vol if pd.notna(max_vol) and max_vol > 0 else 1.0)
    return _zscore(1.0 / vol)


def target_weights(
    df: pd.DataFrame,
    qqq: pd.Series,
    use_market_cap: bool = False,
    tickers: list[str] | None = None,
    max_weight: float = MAX_WEIGHT,
    mcap_cache: pd.Series | None = None,
    mcap_gamma: float | None = None,
) -> tuple[pd.Series, bool]:
    """
    KQPM Alpha 비중 산출 (Z-score 가중합 + 시총 프리셋).

    알고리즘
    --------
    1. 시장 국면 판단
       KODEX 200(069500) 현재가 vs MA200 → Bull / Bear

    2. 팩터 Z-score 계산
       · 모멘텀: 21일(10%)·63일(20%)·126일(30%)·252일(40%) 수익률 Z-score 가중합
       · 변동성역수: 60일 표준편차 역수 Z-score

    3. 국면별 가중 합산 (AQR 방식)
       alpha = 모멘텀_z × m_w  +  변동성역수_z × v_w
         · Bull: m_w=0.7, v_w=0.3  (모멘텀 주도)
         · Bear: m_w=0.4, v_w=0.6  (변동성방어 주도)

    4. 시총 Z-score 혼합 (γ)
       alpha_final = alpha × (1 - γ)  +  mcap_z × γ
         · γ=0.00 : 순수 팩터 (시총 무시)
         · γ=0.15 : 균형  (기본값, MDD 소폭 개선)
         · γ=0.30 : 시총 편향 (집중도 -2.6%p 완화)
       mcap_cache 없거나 조회 실패 시 γ=0으로 자동 fallback.

    5. ReLU → 정규화 → 25% 캡
       음수 alpha 종목은 0으로 clip (자연 배제).
       이후 합=1 정규화 → 25% 상한 클리핑 → 재정규화.

    Parameters
    ----------
    mcap_gamma : 명시 지정 시 MCAP_PRESETS 대신 이 값 사용.
                 백테스트 루프에서 shares_outstanding × 과거주가로 계산된
                 mcap_cache를 넘길 때 활용.
    """
    tickers = tickers or df.columns.tolist()

    # ── 1. 시장 국면 판단 ────────────────────────────────────────
    qqq_last = float(qqq.iloc[-1]) if len(qqq) > 0 and pd.notna(qqq.iloc[-1]) else None
    ma_val   = qqq.rolling(MA_WINDOW).mean().iloc[-1] if len(qqq) >= MA_WINDOW else np.nan

    if qqq_last is None or pd.isna(ma_val):
        is_bull = True   # 데이터 부족 → 강세장으로 안전 처리
    else:
        is_bull = qqq_last > float(ma_val)

    # ── 2. 팩터 Z-score ─────────────────────────────────────────
    f_mom = momentum_score(df)
    f_vol = vol_inv_zscore(df)

    # ── 3. 국면별 가중 합산 ─────────────────────────────────────
    m_w = 0.7 if is_bull else 0.4
    v_w = 0.3 if is_bull else 0.6
    alpha = f_mom * m_w + f_vol * v_w

    # ── 4. 시총 Z-score 혼합 ────────────────────────────────────
    # γ 결정: 명시값 > use_market_cap 프리셋 > 0
    if mcap_gamma is not None:
        gamma = float(mcap_gamma)
    elif use_market_cap:
        gamma = MCAP_PRESETS["balanced"]   # 기본: 균형(15%)
    else:
        gamma = 0.0

    if gamma > 0 and mcap_cache is not None:
        mcap_z = _mcap_zscore(mcap_cache, df.columns)
        alpha  = alpha * (1.0 - gamma) + mcap_z * gamma
    # mcap_cache 없으면 gamma=0 fallback (조회 실패 안전 처리)

    # ── 5. ReLU → 정규화 → 캡 ───────────────────────────────────
    w = alpha.clip(lower=0)
    if w.sum() < 1e-8:
        w = pd.Series(1.0 / len(df.columns), index=df.columns)
    else:
        w = w / w.sum()

    w = w.clip(upper=max_weight)
    w = w / w.sum()

    return w, is_bull


# ──────────────────────────────────────────────
# 매수 추천
# ──────────────────────────────────────────────

def buy_recommendation(
    holdings: dict[str, float],
    budget_krw: float,
    mcap_preset: str = "balanced",
    top_n: int = 10,
    max_weight: float = MAX_WEIGHT,
) -> dict:
    """
    mcap_preset: "factor"(γ=0) | "balanced"(γ=0.15, 기본) | "mcap"(γ=0.30)
    """
    tickers = list(holdings.keys())
    data    = fetch_prices(tickers, extra=[MARKET_BENCHMARK])

    prices_df = data["prices"].reindex(columns=tickers).ffill()
    qqq_s     = data.get(MARKET_BENCHMARK, pd.Series(dtype=float))

    if prices_df.empty or qqq_s.empty:
        raise ValueError("시장 데이터를 불러오지 못했습니다.")

    fx_estimated = False
    fx_rate = KRW_FX_RATE

    curr_p = prices_df.iloc[-1]
    gamma  = MCAP_PRESETS.get(mcap_preset, MCAP_PRESETS["balanced"])

    mcap_ok    = True
    mcap_cache = None
    if gamma > 0:
        mcap_cache, mcap_ok = fetch_market_caps(tickers)
        if not mcap_ok:
            gamma = 0.0   # 시총 조회 실패 → fallback to 순수 팩터

    w_target, is_bull = target_weights(
        prices_df, qqq_s,
        tickers=tickers,
        max_weight=max_weight,
        mcap_cache=mcap_cache,
        mcap_gamma=gamma,
    )

    top_tickers = w_target.nlargest(top_n).index.tolist()
    w_final     = w_target.loc[top_tickers]
    w_final     = w_final / w_final.sum()

    h_series  = pd.Series(holdings).reindex(tickers).fillna(0)
    total_krw = (curr_p * h_series).sum()

    p_safe     = curr_p.reindex(top_tickers).replace(0, np.nan).fillna(1.0)
    raw_budget = w_final * budget_krw
    buy_shares = (raw_budget / p_safe).apply(np.floor).astype(int)
    buy_krw    = buy_shares * p_safe
    # Compatibility key for existing UI code. In the Korean-market app this is planned KRW allocation.
    buy_usd    = raw_budget.copy()

    return {
        "tickers":         top_tickers,
        "weights":         w_final,
        "buy_krw":         buy_krw,
        "buy_usd":         buy_usd,
        "buy_shares":      buy_shares,
        "fx_rate":         fx_rate,
        "fx_estimated":    fx_estimated,
        "mcap_ok":         mcap_ok,
        "mcap_preset":     mcap_preset,
        "mcap_gamma":      gamma,
        "is_bull":         is_bull,
        "prices":          curr_p,
        "total_value_usd": total_krw,
        "total_value_krw": total_krw,
    }


def rebalance_weights(
    holdings: dict[str, float],
    mcap_preset: str = "balanced",
    top_n: int | None = None,
    max_weight: float = MAX_WEIGHT,
) -> dict:
    tickers = list(holdings.keys())
    data    = fetch_prices(tickers, extra=[MARKET_BENCHMARK])

    prices_df = data["prices"].reindex(columns=tickers).ffill()
    qqq_s     = data.get(MARKET_BENCHMARK, pd.Series(dtype=float))

    if prices_df.empty or qqq_s.empty:
        raise ValueError("시장 데이터를 불러오지 못했습니다.")

    fx_estimated = False
    fx_rate = KRW_FX_RATE

    curr_p = prices_df.iloc[-1]
    gamma  = MCAP_PRESETS.get(mcap_preset, MCAP_PRESETS["balanced"])

    mcap_ok    = True
    mcap_cache = None
    if gamma > 0:
        mcap_cache, mcap_ok = fetch_market_caps(tickers)
        if not mcap_ok:
            gamma = 0.0

    w_target, is_bull = target_weights(
        prices_df, qqq_s,
        tickers=tickers,
        max_weight=max_weight,
        mcap_cache=mcap_cache,
        mcap_gamma=gamma,
    )

    if top_n is not None:
        top_t    = w_target.nlargest(top_n).index
        w_target = w_target.loc[top_t]
        w_target = w_target / w_target.sum()

    return {
        "weights":      w_target,
        "prices":       curr_p,
        "fx_rate":      fx_rate,
        "fx_estimated": fx_estimated,
        "mcap_ok":      mcap_ok,
        "mcap_preset":  mcap_preset,
        "mcap_gamma":   gamma,
        "is_bull":      is_bull,
    }


# ──────────────────────────────────────────────
# 백테스트
# ──────────────────────────────────────────────

BENCHMARKS = {
    "069500": "KODEX 200",
    "229200": "KODEX 코스닥150",
}


def run_backtest(
    tickers: list[str],
    weekly_budget: int = 100_000,
    benchmark_tickers: list[str] | None = None,
    period: str = "3y",
    mcap_preset: str = "balanced",
    progress_cb=None,
    top_n: int | None = None,
    start: str | None = None,
    end: str | None = None,
    sim_start: str | None = None,
) -> pd.DataFrame:
    """
    적립식 주간 매수 백테스트.

    mcap_preset : "factor"(γ=0) | "balanced"(γ=0.15, 기본) | "mcap"(γ=0.30)

    신뢰성 체크리스트
    -----------------
    [1] 미래 데이터 유입 차단
        - fetch는 close raw(ffill 없음) 반환
        - 루프 내 prices.iloc[:loc+1].ffill() 슬라이스 후 ffill
        - 팩터 계산(momentum, vol, MA)은 항상 curr_date 이전 데이터만 사용

    [2] 워밍업 버퍼 보장
        - sim_start 기준 420 달력일 앞에서 데이터 fetch
          (420일 ≈ 290 거래일 > 252거래일 워밍업 + 최대 30거래일 휴장 여유)
        - 실제 워밍업 거래일 수 검증 후 부족 시 명확한 에러

    [3] 실제 집행금액 = Invested (과대계상 방지)
        - valid 종목(가격 존재·양수)만 투자 대상
        - 스킵된 종목의 예산을 valid 종목에 재분배 후 재정규화
        - 실제 집행된 KRW를 누적해 Invested에 정확히 반영

    [4] KQPM Alpha ↔ 벤치마크 t=0 통일
        - KH가 실제 투자한 주에만 벤치마크도 동일 금액(week_invested) 투자

    [5] 벤치마크 가격 NaN 처리
        - bm_p_series도 슬라이스 후 ffill 적용
        - 가격 누락 주는 매수 스킵, 이전 평가금 유지(ffill)

    [6] 국장 가격은 KRW 기준, 환율은 1.0 고정
    [7] 시총 근사: 현재 발행주수 × 해당 시점 주가 (look-ahead 최소화)
    """
    benchmark_tickers = benchmark_tickers or list(BENCHMARKS.keys())
    extra = [MARKET_BENCHMARK] + benchmark_tickers

    # ── [2] 워밍업 버퍼: sim_start 기준 420 달력일 앞에서 fetch ──
    if sim_start:
        _sim_ts     = pd.Timestamp(sim_start)
        fetch_start = (_sim_ts - pd.DateOffset(days=420)).strftime("%Y-%m-%d")
    else:
        fetch_start = start  # None이면 period 사용

    data = fetch_prices(
        tickers, extra=extra,
        period=period,
        start=fetch_start,
        end=end,
    )

    # [1] raw close (ffill 미적용) — 루프 내 슬라이스 후 ffill
    prices    = data["prices"]
    qqq       = data.get(MARKET_BENCHMARK, pd.Series(dtype=float))
    fx_full   = pd.Series(KRW_FX_RATE, index=prices.index)
    bm_prices = {bm: data.get(bm, pd.Series(dtype=float)) for bm in benchmark_tickers}

    # ── 시뮬 시작 인덱스 결정 ────────────────────────────────────
    if sim_start:
        _sim_ts        = pd.Timestamp(sim_start)
        sim_start_iloc = int(prices.index.searchsorted(_sim_ts, side="left"))
        start_idx      = sim_start_iloc
    else:
        start_idx = 252

    # [2] 워밍업 거래일 수 검증
    if start_idx < 252:
        raise ValueError(
            f"워밍업 데이터 부족: {start_idx}거래일 확보 (최소 252일 필요).\n"
            f"시뮬레이션 시작일({sim_start})을 최소 1년 더 앞으로 설정하거나, "
            "'2년' 이상의 기간을 선택하세요."
        )
    if len(prices) <= start_idx:
        raise ValueError(
            f"전체 데이터({len(prices)}일)가 시뮬 시작 인덱스({start_idx})보다 적습니다."
        )

    # ── [7] 시총 근사용 발행주수 1회 조회 ────────────────────────
    gamma = MCAP_PRESETS.get(mcap_preset, MCAP_PRESETS["balanced"])
    shares_outstanding: pd.Series | None = None
    if gamma > 0:
        shares_outstanding = fetch_shares_outstanding(tickers)

    # ── 매수 일정: 시뮬 시작일부터 마지막 거래일까지 매주 월요일 ──
    rebal_days = pd.date_range(
        start=prices.index[start_idx],
        end=prices.index[-1],
        freq="W-MON",
    )

    shares_kh = pd.Series(0.0, index=tickers)
    bm_shares = {bm: 0.0 for bm in benchmark_tickers}

    total_invested_krw = 0.0   # [3] 누적 실제 집행액

    history     = []
    total_steps = len(rebal_days)

    for i, rebal_date in enumerate(rebal_days):
        if progress_cb:
            progress_cb(i + 1, total_steps)

        loc = _get_loc_safe(prices.index, rebal_date)
        if loc is None:
            continue
        curr_date = prices.index[loc]

        # [1] 슬라이스 후 ffill: 미래 가격 유입 차단
        df_s = prices.iloc[: loc + 1].ffill()
        cp   = df_s.iloc[-1]

        cfx = KRW_FX_RATE

        # [1] 시장 벤치마크 슬라이스 후 ffill
        qqq_loc = _get_loc_safe(qqq.index, curr_date) if not qqq.empty else None
        qqq_s   = qqq.iloc[: qqq_loc + 1].ffill() if qqq_loc is not None else pd.Series(dtype=float)

        # [7] 시총 근사: 발행주수 × 해당 시점 주가
        mcap_cache_bt = None
        if gamma > 0 and shares_outstanding is not None:
            cp_valid    = cp.reindex(shares_outstanding.index).fillna(0)
            mcap_approx = shares_outstanding * cp_valid
            min_val     = mcap_approx[mcap_approx > 0].min()
            mcap_approx = mcap_approx.replace(0, np.nan).fillna(
                min_val if pd.notna(min_val) else 1.0
            )
            mcap_cache_bt = mcap_approx

        # ── KQPM Alpha 비중 계산 ─────────────────────────────────────
        w_t, _ = target_weights(
            df_s, qqq_s,
            mcap_cache=mcap_cache_bt,
            mcap_gamma=gamma,
        )
        if top_n is not None:
            top_t = w_t.nlargest(top_n).index
            w_t   = w_t.loc[top_t]
            w_t   = w_t / w_t.sum()

        # [3] valid 종목(가격 존재·양수)만 투자, 스킵 예산 재분배
        investable = [
            t for t in w_t.index
            if pd.notna(cp.get(t)) and float(cp.get(t, 0)) > 0
        ]
        week_invested = 0.0
        if investable:
            w_valid   = w_t.loc[investable]
            w_valid   = w_valid / w_valid.sum()   # 재정규화
            alloc_krw = w_valid * weekly_budget

            for t in investable:
                price_krw = float(cp[t])
                buy_qty = int(np.floor(float(alloc_krw[t]) / price_krw))
                if buy_qty <= 0:
                    continue
                buy_krw_t = buy_qty * price_krw
                shares_kh[t] += buy_qty
                week_invested += buy_krw_t

            total_invested_krw += week_invested
        # investable이 비어 있으면 이 주는 투자 스킵 (Invested 미증가)

        # ── 포트폴리오 평가금액 ───────────────────────────────────
        portfolio_value = float((shares_kh * cp.fillna(0)).sum() * cfx)
        row = {
            "Date":        curr_date,
            "QPM_Alpha": portfolio_value,
            "Invested":    total_invested_krw,   # [3] 실제 집행액 누적
        }

        # [4][5] 벤치마크: KH가 투자한 주에만 동일 금액 투자, 슬라이스 후 ffill
        for bm, bm_p_raw in bm_prices.items():
            if bm_p_raw.empty:
                row[bm] = np.nan
                continue
            bm_loc = _get_loc_safe(bm_p_raw.index, curr_date)
            if bm_loc is None:
                row[bm] = np.nan
                continue
            # [5] 벤치마크도 슬라이스 후 ffill
            cp_bm_s = bm_p_raw.iloc[: bm_loc + 1].ffill()
            cp_bm   = float(cp_bm_s.iloc[-1]) if not cp_bm_s.empty else np.nan

            if pd.notna(cp_bm) and cp_bm > 0 and week_invested > 0:
                # [4] KH 실제 집행액과 동일 금액으로 매수
                bm_qty = int(np.floor(week_invested / cp_bm))
                bm_shares[bm] += bm_qty
            row[bm] = bm_shares[bm] * cp_bm * cfx if (pd.notna(cp_bm) and cp_bm > 0) else np.nan

        history.append(row)

    if not history:
        raise ValueError("백테스트 결과가 없습니다. 데이터를 확인하세요.")

    df = pd.DataFrame(history).set_index("Date")
    # 벤치마크 NaN(가격 누락 주)만 ffill, Invested·KH는 정확한 값이므로 그대로 유지
    bm_cols = [c for c in df.columns if c not in ("QPM_Alpha", "Invested")]
    df[bm_cols] = df[bm_cols].ffill()
    return df
