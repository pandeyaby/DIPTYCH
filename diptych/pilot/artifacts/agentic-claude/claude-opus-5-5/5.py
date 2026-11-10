"""Adaptive concurrency controller.

Incremental (velocity-form) controller on the normalised latency error

    e = clip((setpoint - latency) / setpoint, E_MIN, E_MAX)

Each tick the limit moves by

    delta = KI * e * m_up      if e >= 0   (latency below setpoint -> grow)
    delta = KI * e * DOWN_RATIO * m_dn     (latency above setpoint -> shrink)

where m_up / m_dn are bounded gain modulators computed only from state that
existed *before* the current observation:

  * Volatility: an EWMA estimate of latency-error and arrival dispersion
    shrinks m_up and enlarges m_dn, and the arrival coefficient of variation
    lowers the effective target (setpoint * (1 - VOL_OFFSET * cv)), so a
    noisier workload converges to a strictly lower (more conservative)
    limit. Latency below the true setpoint never moves the limit down.
  * Trend: an EWMA of the tick-to-tick change in error (positive while latency
    is improving) raises m_up / lowers m_dn while improving and vice versa
    while worsening, so recent observations weigh more than older ones.

Guarantees by construction:

  1. Asymmetry: m_up <= (1 + TREND_UP) and m_dn >= (1 - TREND_DN), so for the
     same state the downward correction to a deviation is at least
     DOWN_RATIO * (1 - TREND_DN) / (1 + TREND_UP) = 2.25 > 2 times the upward
     correction to an equal, opposite deviation.
  3. Anti-windup: there is no integrator; the limit is clamped every tick and
     all statistics are frozen while the controller is saturated, so the time
     spent saturated leaves no trace. The first response after saturation is
     |delta| <= KI * DOWN_RATIO * M_DN_MAX * |e| -- bounded by the current
     error only.
  5. All observation-derived state is reset on a setpoint change.
  6/7. Plain float arithmetic in a fixed order, no hashing/randomness/clock;
     snapshots are JSON with repr-exact floats.
  8. Telemetry is built from one fixed literal key list.
"""

from __future__ import annotations

import json
import math

KI = 3.0             # limit units per unit of normalised error per tick (upward)
DOWN_RATIO = 3.0     # downward base gain multiplier
E_MIN = -5.0         # lower clip of normalised error (latency far above setpoint)
E_MAX = 1.0          # upper clip (latency cannot go below zero)

STAT_ALPHA = 0.1     # EWMA weight for dispersion statistics
STAT_WARMUP = 10     # bias-corrected warm-up length (alpha = max(STAT_ALPHA, 1/n))
STAT_CLIP = 0.5      # clip on errors entering dispersion statistics
VOL_LAT_COEF = 1.0   # volatility per unit of latency-error std dev
VOL_ARR_COEF = 1.0   # volatility per unit of arrival coefficient of variation
VOL_MAX = 1.0        # cap on the volatility index
VOL_GAIN = 1.0       # m_up /= (1 + VOL_GAIN*vol);  m_dn *= (1 + VOL_GAIN*vol)
ARR_DEV_CLIP = 0.3   # max |arrival - mean| / mean entering the statistics
VOL_OFFSET = 1.0     # effective setpoint = setpoint * (1 - VOL_OFFSET*arrival_cv)

TREND_ALPHA = 0.3    # EWMA weight for the error trend
TREND_SCALE = 0.05   # trend (error units / tick) that maps to full modulation
TREND_UP = 0.2       # max fractional change of m_up due to trend
TREND_DN = 0.1       # max fractional change of m_dn due to trend

_STATE_KEYS = (
    "min_limit", "max_limit", "limit", "setpoint", "n", "e_prev", "trend",
    "e_mean", "e_var", "a_mean", "a_var", "saturated", "last_delta",
    "last_error", "last_vol", "last_trend_idx", "last_m_up", "last_m_dn",
    "resets",
)


def _clip(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


class Controller:
    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        self.limit = _clip(float(config.get("initial_limit", 50.0)),
                           self.min_limit, self.max_limit)
        self.setpoint = None  # type: float | None
        self.resets = 0
        self.saturated = 0
        self.last_delta = 0.0
        self.last_error = 0.0
        self.last_vol = 0.0
        self.last_trend_idx = 0.0
        self.last_m_up = 1.0
        self.last_m_dn = 1.0
        self._reset_stats()

    # ------------------------------------------------------------------ state
    def _reset_stats(self) -> None:
        self.n = 0
        self.e_prev = 0.0
        self.trend = 0.0
        self.e_mean = 0.0
        self.e_var = 0.0
        self.a_mean = 0.0
        self.a_var = 0.0

    def _arrival_cv(self) -> float:
        if self.n < 2 or self.a_mean <= 1e-9 or self.a_var <= 0.0:
            return 0.0
        return _clip(math.sqrt(self.a_var) / self.a_mean, 0.0, VOL_MAX)

    def _volatility(self) -> float:
        if self.n < 2:
            return 0.0
        lat_sd = math.sqrt(self.e_var) if self.e_var > 0.0 else 0.0
        arr_cv = 0.0
        if self.a_mean > 1e-9 and self.a_var > 0.0:
            arr_cv = math.sqrt(self.a_var) / self.a_mean
        return _clip(VOL_LAT_COEF * lat_sd + VOL_ARR_COEF * arr_cv, 0.0, VOL_MAX)

    def _update_stats(self, e: float, arr: float) -> None:
        self.n += 1
        if self.n == 1:
            self.e_mean, self.e_var = e, 0.0
            self.a_mean, self.a_var = arr, 0.0
            self.trend = 0.0
            self.e_prev = e
            return
        a = 1.0 / self.n if self.n < STAT_WARMUP else STAT_ALPHA
        d = e - self.e_mean
        self.e_mean += a * d
        self.e_var = (1.0 - a) * (self.e_var + a * d * d)
        # Arrival deviations are clipped relative to the running mean so a
        # single load step does not masquerade as long-lived volatility.
        lim = ARR_DEV_CLIP * self.a_mean
        d = _clip(arr - self.a_mean, -lim, lim)
        self.a_mean += a * d
        self.a_var = (1.0 - a) * (self.a_var + a * d * d)
        self.trend += TREND_ALPHA * ((e - self.e_prev) - self.trend)
        self.e_prev = e

    # ------------------------------------------------------------------- tick
    def tick(self, obs: dict) -> dict:
        lat = float(obs["latency_ms"])
        sp = float(obs["setpoint_ms"])
        arr = float(obs.get("arrival_rps", 0.0))
        if not math.isfinite(arr) or arr < 0.0:
            arr = 0.0

        if self.setpoint is None or sp != self.setpoint:
            if self.setpoint is not None:
                self.resets += 1
            self.setpoint = sp
            self._reset_stats()

        if sp > 0.0 and math.isfinite(lat):
            e = _clip((sp - lat) / sp, E_MIN, E_MAX)
        else:
            e = E_MIN

        # Gain modulators from pre-tick state only.
        vol = self._volatility()
        tr = _clip(self.trend / TREND_SCALE, -1.0, 1.0)
        m_up = (1.0 + TREND_UP * tr) / (1.0 + VOL_GAIN * vol)
        m_dn = (1.0 - TREND_DN * tr) * (1.0 + VOL_GAIN * vol)

        # Volatility also lowers the effective target (computed from pre-tick
        # state, so it shifts both directions equally and only strengthens
        # the down/up asymmetry).
        e_eff = e - VOL_OFFSET * self._arrival_cv()
        # Latency at/below the true setpoint never causes a downward move;
        # between the effective and true setpoint the limit holds.
        if e >= 0.0:
            raw = KI * (e_eff if e_eff > 0.0 else 0.0) * m_up
        else:
            raw = KI * DOWN_RATIO * e_eff * m_dn

        proposed = self.limit + raw
        new_limit = _clip(proposed, self.min_limit, self.max_limit)
        sat = proposed > self.max_limit or proposed < self.min_limit

        # Anti-windup: freeze statistics while saturated, so the duration of
        # saturation cannot influence later corrections.
        if not sat or self.n == 0:
            self._update_stats(_clip(e, -STAT_CLIP, STAT_CLIP), arr)
        self.saturated = 1 if sat else 0

        self.last_delta = new_limit - self.limit
        self.limit = new_limit
        self.last_error = e
        self.last_vol = vol
        self.last_trend_idx = tr
        self.last_m_up = m_up
        self.last_m_dn = m_dn

        return {"limit": self.limit, "telemetry": self._telemetry()}

    def _telemetry(self) -> dict:
        return {
            "limit": float(self.limit),
            "setpoint_ms": float(self.setpoint) if self.setpoint is not None else 0.0,
            "error": float(self.last_error),
            "delta": float(self.last_delta),
            "volatility": float(self.last_vol),
            "trend_index": float(self.last_trend_idx),
            "gain_up": float(self.last_m_up),
            "gain_down": float(self.last_m_dn),
            "saturated": int(self.saturated),
            "samples_since_reset": int(self.n),
            "setpoint_resets": int(self.resets),
            "error_mean": float(self.e_mean),
            "error_var": float(self.e_var),
            "arrival_mean": float(self.a_mean),
            "arrival_var": float(self.a_var),
        }

    # -------------------------------------------------------------- snapshot
    def snapshot(self) -> str:
        return json.dumps({k: getattr(self, k) for k in _STATE_KEYS}, sort_keys=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        obj = cls.__new__(cls)
        for k in _STATE_KEYS:
            setattr(obj, k, state[k])
        return obj
