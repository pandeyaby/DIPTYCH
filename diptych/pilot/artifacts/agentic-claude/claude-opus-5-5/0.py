"""Adaptive concurrency controller.

Each tick the limit is multiplied by (1 + g * asym(s(e))), where

    e       = (target - latency) / setpoint, clipped to [-1, 1]
              (positive: latency below target, room to admit more)
    s(e)    = e * (1 + BOOST * |e|)   (odd, so equal-magnitude errors
              are shaped identically in both directions)
    asym(x) = x for x >= 0, DOWN_RATIO * x for x < 0
    g_up    = KI * (1 + TREND_GAIN * agree_up)   / vf
    g_down  = KI * (1 + TREND_GAIN * agree_down) * vf * af

Everything except e is computed from state built from *previous* observations,
so the response to the current observation is exactly proportional to
asym(s(e)), and a tick on target never moves the limit.

  * Asymmetry: the down/up ratio is at least DOWN_RATIO / (1 + TREND_GAIN) > 2
    in every state (vf, af >= 1 only widen it). With steady offered load the
    target equals the setpoint, so this holds for deviations from the setpoint.
  * Trend recency: agree_up / agree_down in [0, 1] measure how strongly the
    recent error trend (EWMA of successive differences) points up / down.
    Corrections that agree with the recent trend are amplified, so recent
    observations outweigh older ones.
  * Volatility suppression: cv is the coefficient of variation of offered load
    (from successive differences, so level shifts do not register as noise)
    and sd the volatility of the latency error. Noisier load lowers the target
    (target = setpoint * (1 - ARR_OFFSET * cv)), af = 1 + ARR_GAIN * cv
    amplifies downward corrections and vf = 1 + VOL_GAIN * sd damps upward /
    amplifies downward ones, so the loop settles at a lower limit on
    higher-variance workloads.

The limit is stored as anchor * prod(factors since the last reset). A reset
makes the anchor the current limit and returns every accumulator to its
initial value. Resets happen when
  * the setpoint changes: later corrections depend only on observations made
    after the change, exactly as for a fresh controller started at that limit;
  * the output clamps at min_limit or max_limit (anti-windup): the state after
    one saturated tick equals the state after many, so the first response to
    an excursion depends only on the error observed then.

State is plain floats/ints serialised as JSON (floats round-trip exactly), and
nothing depends on hashing, randomness or wall-clock time.
"""

from __future__ import annotations

import json
import math

VERSION = 4

ERR_CLIP = 1.0        # clip normalised error to [-ERR_CLIP, ERR_CLIP]
DOWN_RATIO = 4.0      # downward/upward weight before gain modulation
KI = 0.04             # base gain (fraction of limit per unit error per tick)
BOOST = 2.0           # extra gain for large errors: e * (1 + BOOST * |e|)
TREND_GAIN = 0.5      # max extra gain for trend-agreeing corrections
TREND_ALPHA = 0.3     # EWMA weight for the error trend
TREND_SCALE = 0.05    # trend magnitude giving full agreement
VOL_GAIN = 1.0        # vf increase per unit latency-error sd
VOL_ALPHA = 0.1       # EWMA weight for the latency-error volatility
ARR_GAIN = 2.0        # af increase per unit arrival cv (downward only)
ARR_OFFSET = 1.0      # target reduction (fraction of setpoint) per unit arrival cv
ARR_MEAN_ALPHA = 0.2  # EWMA weight for the arrival mean
ARR_VAR_ALPHA = 0.05  # EWMA weight for the arrival variance
SD_CAP = 1.0          # cap on sd and cv
MIN_TARGET = 0.5      # target never drops below this fraction of the setpoint
MIN_FACTOR = 0.5      # lower bound on the per-tick multiplicative factor

_STATE_KEYS = ("min_limit", "max_limit", "anchor", "prod", "trend", "vol",
               "prev_e", "arr_mean", "arr_var", "prev_arr", "last_sp", "limit",
               "n", "resets")


class Controller:
    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        init = float(config.get("initial_limit", 50.0))
        init = min(max(init, self.min_limit), self.max_limit)
        self.anchor = init        # limit at the last reset
        self.prod = 1.0           # product of per-tick factors since reset
        self.trend = 0.0          # EWMA of successive error differences
        self.vol = 0.0            # EWMA of squared successive error differences
        self.prev_e = None        # previous error (None after reset)
        self.arr_mean = 0.0       # EWMA of arrival_rps
        self.arr_var = 0.0        # EWMA of half squared successive arrival diffs
        self.prev_arr = None      # previous arrival_rps (None after reset)
        self.last_sp = None       # setpoint seen on the previous tick
        self.limit = init         # last output
        self.n = 0                # observations since last reset
        self.resets = 0           # count of resets

    def _reset(self, anchor: float) -> None:
        self.anchor = anchor
        self.prod = 1.0
        self.trend = 0.0
        self.vol = 0.0
        self.prev_e = None
        self.arr_mean = 0.0
        self.arr_var = 0.0
        self.prev_arr = None
        self.n = 0
        self.resets += 1

    def tick(self, obs: dict) -> dict:
        sp = float(obs["setpoint_ms"])
        lat = float(obs["latency_ms"])
        arr = float(obs.get("arrival_rps", 0.0))
        if not (sp > 0.0 and math.isfinite(sp)):
            sp = 1e-9
        if math.isnan(lat):
            lat = sp * (1.0 + ERR_CLIP)

        sp_changed = self.last_sp is not None and sp != self.last_sp
        if sp_changed:
            self._reset(self.limit)
        self.last_sp = sp

        # Modulation terms from previous observations only.
        agree = min(max(self.trend / TREND_SCALE, -1.0), 1.0)
        sd = min(math.sqrt(self.vol / 2.0), SD_CAP)
        cv = 0.0
        if self.arr_mean > 0.0:
            cv = min(math.sqrt(self.arr_var) / self.arr_mean, SD_CAP)
        vf = 1.0 + VOL_GAIN * sd
        af = 1.0 + ARR_GAIN * cv
        target = sp * max(1.0 - ARR_OFFSET * cv, MIN_TARGET)

        e = (target - lat) / sp
        e = min(max(e, -ERR_CLIP), ERR_CLIP)
        shaped = e * (1.0 + BOOST * abs(e))   # odd: same shape both ways
        if e >= 0.0:
            gain = KI * (1.0 + TREND_GAIN * max(agree, 0.0)) / vf
            step = gain * shaped
        else:
            gain = KI * (1.0 + TREND_GAIN * max(-agree, 0.0)) * vf * af
            step = gain * DOWN_RATIO * shaped
        factor = max(1.0 + step, MIN_FACTOR)
        self.prod *= factor

        # Fold the current observation into the history.
        if self.prev_e is not None:
            d = e - self.prev_e
            self.trend = (1.0 - TREND_ALPHA) * self.trend + TREND_ALPHA * d
            self.vol = (1.0 - VOL_ALPHA) * self.vol + VOL_ALPHA * d * d
        self.prev_e = e
        if math.isfinite(arr) and arr >= 0.0:
            if self.prev_arr is None:
                self.arr_mean = arr
            else:
                da = arr - self.prev_arr
                self.arr_mean += ARR_MEAN_ALPHA * (arr - self.arr_mean)
                self.arr_var = ((1.0 - ARR_VAR_ALPHA) * self.arr_var
                                + ARR_VAR_ALPHA * 0.5 * da * da)
            self.prev_arr = arr
        self.n += 1

        raw = self.anchor * self.prod
        saturated = 0
        if raw >= self.max_limit:
            out, saturated = self.max_limit, 1
        elif raw <= self.min_limit:
            out, saturated = self.min_limit, -1
        else:
            out = raw
        if saturated:
            self._reset(out)
        self.limit = out

        telemetry = {
            "t": int(obs.get("t", 0)),
            "error": e,
            "target_ms": target,
            "gain": gain,
            "factor": factor,
            "trend": self.trend,
            "volatility_sd": sd,
            "arrival_cv": cv,
            "vol_factor": vf,
            "arrival_factor": af,
            "anchor": self.anchor,
            "raw_limit": raw,
            "saturated": saturated,
            "setpoint_changed": 1 if sp_changed else 0,
            "samples": self.n,
            "resets": self.resets,
        }
        return {"limit": out, "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {k: getattr(self, k) for k in _STATE_KEYS}
        state["version"] = VERSION
        return json.dumps(state, sort_keys=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        obj = cls.__new__(cls)
        for k in _STATE_KEYS:
            setattr(obj, k, state[k])
        return obj
