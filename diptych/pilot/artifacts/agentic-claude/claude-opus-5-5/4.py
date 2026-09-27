"""Adaptive concurrency controller.

PI controller on the relative latency error, with:

* asymmetric gains (downward corrections are ``DOWN_RATIO`` times stronger
  than upward ones for the same error magnitude),
* recency terms that only ever push downward: a proportional term on the
  positive part of the latest error, and a TREND_BOOST on
  downward steps taken while latency is worsening,
* no stored integrator: the limit itself is the integrator state and is
  clamped every tick, so saturation cannot wind anything up,
* a latency-target margin and an upward-step cut, both proportional to the
  observed coefficient of variation of the offered load, so volatile load
  settles at a lower limit,
* a full reset of all filter state whenever the setpoint changes.

Pure standard-library arithmetic; no randomness, clocks, or hash-dependent
iteration, so decisions are bit-reproducible and snapshots round-trip exactly.
"""

from __future__ import annotations

import json
import math

KI_UP = 5.0          # integral gain (limit units per unit relative error), upward
KP_UP = 1.5          # proportional gain on the latest positive error (< KI_UP / 2)
TREND_BOOST = 0.4    # extra downward step fraction while latency is rising
DOWN_RATIO = 3.0     # downward gains = DOWN_RATIO * upward gains (> 2)
ERR_MIN = -2.0       # clamp on relative error (latency far above setpoint)
ERR_MAX = 1.0        # clamp on relative error (latency far below setpoint)
VOL_ALPHA = 0.02     # EWMA weight for load mean/variance estimates
VOL_GAIN = 4.0       # upward-step shrink per unit of load coefficient of variation
VOL_MARGIN = 0.5     # latency-target cut per unit of load coefficient of variation
VOL_MARGIN_MAX = 0.3  # never cut the target by more than 30%

_STATE_KEYS = (
    "min_limit", "max_limit", "limit", "prev_lat", "prev_p", "last_setpoint",
    "arr_mean", "arr_var", "prev_arr", "n_since_reset",
    "sat_ticks", "resets",
)


class Controller:
    def __init__(self, config: dict):
        self.min_limit = float(config["min_limit"])
        self.max_limit = float(config["max_limit"])
        self.limit = self._clamp(float(config["initial_limit"]))
        self._reset_filters()
        self.last_setpoint = None
        self.sat_ticks = 0
        self.resets = 0

    # ------------------------------------------------------------------ helpers
    def _clamp(self, x: float) -> float:
        return min(max(x, self.min_limit), self.max_limit)

    def _reset_filters(self) -> None:
        self.prev_lat = 0.0
        self.prev_p = 0.0
        self.arr_mean = 0.0
        self.arr_var = 0.0
        self.prev_arr = 0.0
        self.n_since_reset = 0

    @staticmethod
    def _shape(err: float, up_scale: float) -> float:
        """Asymmetric error shaping: negative (over-latency) errors amplified,
        positive ones scaled by ``up_scale`` <= 1."""
        err = min(max(err, ERR_MIN), ERR_MAX)
        return up_scale * err if err >= 0.0 else DOWN_RATIO * err

    # --------------------------------------------------------------------- tick
    def tick(self, obs: dict) -> dict:
        lat = float(obs["latency_ms"])
        sp = float(obs["setpoint_ms"])
        arr = float(obs["arrival_rps"])

        reset = self.last_setpoint is not None and sp != self.last_setpoint
        if reset:
            self._reset_filters()
            self.resets += 1
        self.last_setpoint = sp

        # Load volatility: EWMA of squared successive differences of arrivals
        # (E[(x_n - x_{n-1})^2] = 2 var for i.i.d. load), so a one-off level
        # step contributes only a single decaying sample.
        if self.n_since_reset == 0:
            self.arr_mean, self.arr_var = arr, 0.0
        else:
            d = arr - self.prev_arr
            self.arr_mean += VOL_ALPHA * (arr - self.arr_mean)
            self.arr_var += VOL_ALPHA * (0.5 * d * d - self.arr_var)
        self.prev_arr = arr
        self.n_since_reset += 1

        arr_cv = math.sqrt(self.arr_var) / self.arr_mean if self.arr_mean > 1e-9 else 0.0
        # Volatile load -> a lower effective latency target and smaller
        # upward steps, so the loop settles at a more conservative limit.
        # Both only ever bias corrections downward.
        margin = min(VOL_MARGIN * arr_cv, VOL_MARGIN_MAX)
        up_scale = 1.0 / (1.0 + VOL_GAIN * arr_cv)
        target = sp * (1.0 - margin)

        err = (target - lat) / sp if sp > 0.0 else 0.0
        g = self._shape(err, up_scale)

        # Recency, two ways, both of which only ever push *downward* so they
        # cannot erode the up/down asymmetry:
        #  * trend: relative latency improvement since the previous tick (0
        #    on the first tick after a reset); a downward step taken while
        #    latency is worsening is boosted;
        #  * a proportional term on the positive part of the latest error, so
        #    the most recent good observation lifts the limit.
        trend = (self.prev_lat - lat) / sp if self.n_since_reset > 1 and sp > 0.0 else 0.0
        boost = 1.0 + TREND_BOOST if g < 0.0 and trend < 0.0 else 1.0
        p = max(g, 0.0)

        # PI in velocity form, with the limit itself as the integrator;
        # clamping it every tick is the anti-windup.  The step depends only on
        # the current observation and the previous one, never on time spent
        # saturated.
        delta = KI_UP * boost * g + KP_UP * (p - self.prev_p)
        self.limit = self._clamp(self.limit + delta)
        self.prev_lat = lat
        self.prev_p = p

        saturated = self.limit >= self.max_limit or self.limit <= self.min_limit
        self.sat_ticks = self.sat_ticks + 1 if saturated else 0

        telemetry = {
            "error": err,
            "shaped_error": g,
            "trend": trend,
            "trend_boost": boost,
            "delta": delta,
            "up_scale": up_scale,
            "target_ms": target,
            "vol_margin": margin,
            "arrival_cv": arr_cv,
            "saturated": 1 if saturated else 0,
            "saturated_ticks": self.sat_ticks,
            "setpoint_reset": 1 if reset else 0,
            "resets": self.resets,
            "ticks_since_reset": self.n_since_reset,
        }
        return {"limit": self.limit, "telemetry": telemetry}

    # ------------------------------------------------------------ persistence
    def snapshot(self) -> str:
        return json.dumps({k: getattr(self, k) for k in _STATE_KEYS}, sort_keys=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        obj = cls.__new__(cls)
        for k in _STATE_KEYS:
            setattr(obj, k, state[k])
        return obj
