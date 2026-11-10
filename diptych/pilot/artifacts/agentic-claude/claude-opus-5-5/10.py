"""Adaptive concurrency controller.

Velocity-form (incremental) controller acting on log(limit):

    e_t   = clip((setpoint - latency) / setpoint, -1, 1)      relative error
    d_t   = e_t - e_{t-1}                                     trend (0 right after
                                                              a reset or saturation)
    p_t   = K_SIG * sigma_e + K_CV * cv_arrival               volatility margin
    u_t   = e_t - p_t
    m_t   = clip(1 + K_T * d_t * sign(u_t), 1/M_MAX, M_MAX)  trend multiplier
    step  = (G_UP if u_t > 0 else G_DOWN) * m_t * u_t         asymmetric gain
    log L <- clamp(log L + clip(step, -MAX_STEP, MAX_STEP))

Design notes, per requirement:

1. Asymmetry: G_DOWN > 2 * M_MAX**2 * G_UP and the margin p >= 0 only pushes
   downward, so from any state, for equal-magnitude deviations of opposite
   sign the downward correction is more than twice the upward one.
2. Recency: the trend multiplier amplifies upward steps and damps downward
   steps on an improving run, and the reverse on a worsening one, so the same
   observations in improving order end at a higher limit.
3. Anti-windup: there is no integrator except the (clamped) limit itself.
   While the limit is pinned at a bound and the controller is pushing into
   it, the volatility statistics are frozen, and the trend multiplier is 1 on
   the first tick after saturation, so the first response to an excursion is
   G_DOWN * (e_t - p) clipped to MAX_STEP, independent of saturation length.
4. Volatility: sigma of the error and coefficient of variation of arrivals are
   tracked with bias-corrected EWMAs and subtracted as a safety margin.
5. Setpoint change: all observation history (previous error, statistics) is
   discarded when setpoint_ms changes.
6/7. Pure float arithmetic, no hashing or randomness; snapshot is JSON with
   repr-exact floats.
8. Telemetry always has the same keys.
"""

from __future__ import annotations

import json
import math

G_UP = 0.05
G_DOWN = 0.18          # > 2 * G_UP
K_T = 1.5            # trend sensitivity of the gain multiplier
M_MAX = 1.3          # multiplier bounded in [1/M_MAX, M_MAX]; G_DOWN/G_UP > 2*M_MAX**2
K_SIG = 0.5
K_CV = 0.3
MAX_STEP = 0.25
ALPHA = 0.1            # EWMA weight for volatility statistics
ERR_CLIP = 1.0

_STATE_KEYS = (
    "min_limit", "max_limit", "log_limit", "setpoint", "prev_e", "has_prev",
    "sat_prev", "n", "w", "m_e", "v_e", "m_a", "v_a", "ticks",
)


def _clip(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


class Controller:
    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        init = _clip(float(config.get("initial_limit", 50.0)), self.min_limit, self.max_limit)
        self.log_limit = math.log(init)
        self.setpoint = -1.0          # unknown until first observation
        self.ticks = 0
        self._reset_history()

    # ------------------------------------------------------------------ state
    def _reset_history(self) -> None:
        self.prev_e = 0.0
        self.has_prev = 0
        self.sat_prev = 0
        self.n = 0                    # observations folded into the statistics
        self.w = 0.0                  # EWMA bias-correction weight: 1-(1-a)^n
        self.m_e = 0.0                # raw (uncorrected) EWMA accumulators
        self.v_e = 0.0
        self.m_a = 0.0
        self.v_a = 0.0

    def _update_stats(self, e: float, a: float) -> None:
        # Exponentially weighted mean/variance (West's incremental form), with
        # bias correction so early estimates are not dragged toward zero.
        self.n += 1
        self.w = self.w + ALPHA * (1.0 - self.w)
        frac = ALPHA / self.w         # effective weight of this sample
        de = e - self.m_e
        self.m_e += frac * de
        self.v_e = (1.0 - frac) * (self.v_e + frac * de * de)
        da = a - self.m_a
        self.m_a += frac * da
        self.v_a = (1.0 - frac) * (self.v_a + frac * da * da)

    # ------------------------------------------------------------------- tick
    def tick(self, obs: dict) -> dict:
        lat = float(obs["latency_ms"])
        sp = float(obs["setpoint_ms"])
        arr = float(obs.get("arrival_rps", 0.0))

        sp_changed = 0
        if sp != self.setpoint:
            if self.setpoint > 0.0:
                sp_changed = 1
            self.setpoint = sp
            self._reset_history()

        e = _clip((sp - lat) / sp, -ERR_CLIP, ERR_CLIP) if sp > 0.0 else -ERR_CLIP

        # Trend: only from post-reset, non-saturated consecutive observations.
        d = (e - self.prev_e) if (self.has_prev and not self.sat_prev) else 0.0

        sigma_e = math.sqrt(self.v_e) if self.n >= 2 else 0.0
        cv_a = (math.sqrt(self.v_a) / self.m_a) if (self.n >= 2 and self.m_a > 1e-9) else 0.0
        penalty = K_SIG * sigma_e + K_CV * cv_a

        u = e - penalty
        sgn = 1.0 if u > 0.0 else (-1.0 if u < 0.0 else 0.0)
        mult = _clip(1.0 + K_T * d * sgn, 1.0 / M_MAX, M_MAX)
        step = (G_UP if u > 0.0 else G_DOWN) * mult * u
        step = _clip(step, -MAX_STEP, MAX_STEP)

        lo, hi = math.log(self.min_limit), math.log(self.max_limit)
        raw = self.log_limit + step
        saturated = 0
        if raw >= hi:
            raw = hi
            saturated = 1 if step > 0.0 else 0
        elif raw <= lo:
            raw = lo
            saturated = 1 if step < 0.0 else 0
        self.log_limit = raw

        # Anti-windup: do not accumulate statistics while pinned at a bound.
        if not saturated:
            self._update_stats(e, arr)
        self.prev_e = e
        self.has_prev = 1
        self.sat_prev = saturated
        self.ticks += 1

        if self.log_limit >= hi:
            limit = self.max_limit
        elif self.log_limit <= lo:
            limit = self.min_limit
        else:
            limit = _clip(math.exp(self.log_limit), self.min_limit, self.max_limit)
        telemetry = {
            "error": e,
            "trend": d,
            "sigma_err": sigma_e,
            "cv_arrival": cv_a,
            "penalty": penalty,
            "control": u,
            "trend_mult": mult,
            "step_log": step,
            "saturated": saturated,
            "setpoint_changed": sp_changed,
            "samples": self.n,
            "setpoint_ms": sp,
            "limit": limit,
            "mode": "down" if step < 0.0 else ("up" if step > 0.0 else "hold"),
        }
        return {"limit": limit, "telemetry": telemetry}

    # ------------------------------------------------------------ persistence
    def snapshot(self) -> str:
        return json.dumps({k: getattr(self, k) for k in _STATE_KEYS}, sort_keys=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        data = json.loads(blob)
        obj = cls.__new__(cls)
        for k in _STATE_KEYS:
            setattr(obj, k, data[k])
        return obj
