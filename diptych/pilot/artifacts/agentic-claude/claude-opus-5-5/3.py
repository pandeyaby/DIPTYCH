"""Adaptive concurrency controller.

The limit is ``clamp(base + trend_adj)``:

* ``base`` is an incremental (velocity-form) integrator driven by the
  normalized latency error ``e = (latency - setpoint) / setpoint`` (clipped to
  ``[-1, 1]``), biased by a volatility estimate so that noisier workloads
  settle at a lower limit::

      s = e + VOL_GAIN * vol

  ``base`` moves down by ``DOWN_GAIN * s`` when ``s > 0`` and up by
  ``UP_GAIN * |s|`` otherwise.
* ``trend_adj`` is a proportional term on the latency *trend*, the difference
  between a fast and a slow EWMA of ``e``. It weights recent observations more
  than older ones, so an improving run ends higher than the same observations
  in worsening order, even when ``base`` is pinned at a bound.
* Asymmetry: both terms use a downward gain 3x the upward gain (per-tick caps
  in ratio 4). Because ``trend_adj`` also carries history, an upward
  correction is additionally capped, from the same state, at ``1 / 2.5`` of
  the downward correction the controller would make to the mirrored deviation
  (``-e``). So from any state the downward correction to a deviation strictly
  exceeds twice the upward correction to an equal and opposite one (unless
  the limit is pinned at a bound or history already forces a rise).
* Anti-windup: ``base`` is clamped to ``[min_limit, max_limit]`` every tick, so
  saturation accumulates nothing; the first response after saturation depends
  on the current observation (and the converged EWMAs), never on how long the
  controller was saturated.
* On a setpoint change the current limit is folded into ``base`` and every
  history-derived statistic is reset, so later corrections depend only on
  observations made after the change (the controller then behaves exactly
  like a fresh one whose ``initial_limit`` is the current limit).

State is a flat dict of floats/ints serialized as JSON (floats round-trip
exactly), so decisions are deterministic and restorable bit-for-bit.
"""

from __future__ import annotations

import json
import math

UP_GAIN = 6.0              # rps of base limit per unit of (negative) signal
DOWN_GAIN = 3.0 * UP_GAIN  # rps of base limit per unit of (positive) signal
UP_CAP = 6.0               # max upward base step per tick (rps)
DOWN_CAP = 4.0 * UP_CAP    # max downward base step per tick (rps)
TREND_UP_GAIN = 6.0        # rps of trend adjustment per unit of improving trend
TREND_DOWN_GAIN = 3.0 * TREND_UP_GAIN
TREND_UP_CAP = 6.0
TREND_DOWN_CAP = 4.0 * TREND_UP_CAP
MIRROR_RATIO = 2.5         # up move <= (mirrored down move) / MIRROR_RATIO
FAST_ALPHA = 0.5           # EWMA weight of the newest error (fast trend)
SLOW_ALPHA = 0.1           # EWMA weight of the newest error (slow trend)
VOL_ALPHA = 0.1            # EWMA weight for volatility statistics
VOL_GAIN = 0.1             # how strongly volatility lowers the effective target
CV_WEIGHT = 0.25           # weight of offered-load CV in the volatility estimate
CV_CLIP = 0.5              # cap on the offered-load CV
ERR_CLIP = 1.0             # normalized error is clipped to [-ERR_CLIP, ERR_CLIP]

_STATE_KEYS = (
    "min_limit", "max_limit", "base", "limit", "setpoint", "n", "fast", "slow",
    "trend_adj", "err_mean", "err_var", "arr_mean", "arr_var", "vol",
    "last_err", "last_signal", "last_step", "last_capped",
)
_HISTORY_DEFAULTS = (
    ("n", 0), ("fast", 0.0), ("slow", 0.0), ("trend_adj", 0.0),
    ("err_mean", 0.0), ("err_var", 0.0), ("arr_mean", 0.0), ("arr_var", 0.0),
    ("vol", 0.0),
)


def _asym(x: float, up_gain: float, up_cap: float, down_gain: float, down_cap: float) -> float:
    """Map a signal (positive = latency too high) to a limit change."""
    if x > 0.0:
        return -min(down_gain * x, down_cap)
    return min(up_gain * -x, up_cap)


def _clamp(st: dict, x: float) -> float:
    return min(max(x, st["min_limit"]), st["max_limit"])


def _advance(st: dict, e: float, arr: float) -> dict:
    """Pure state transition for one normalized error ``e``; returns new state."""
    st = dict(st)
    if st["n"] == 0:
        st["fast"] = e
        st["slow"] = e
        st["err_mean"] = e
        st["err_var"] = 0.0
        st["arr_mean"] = arr
        st["arr_var"] = 0.0
    else:
        st["fast"] += FAST_ALPHA * (e - st["fast"])
        st["slow"] += SLOW_ALPHA * (e - st["slow"])
        d = e - st["err_mean"]
        st["err_mean"] += VOL_ALPHA * d
        st["err_var"] = (1.0 - VOL_ALPHA) * (st["err_var"] + VOL_ALPHA * d * d)
        da = arr - st["arr_mean"]
        st["arr_mean"] += VOL_ALPHA * da
        st["arr_var"] = (1.0 - VOL_ALPHA) * (st["arr_var"] + VOL_ALPHA * da * da)
    st["n"] += 1

    am = st["arr_mean"]
    cv = math.sqrt(st["arr_var"]) / am if am > 1e-9 else 0.0
    st["vol"] = math.sqrt(st["err_var"]) + CV_WEIGHT * min(cv, CV_CLIP)

    s = e + VOL_GAIN * st["vol"]
    step = _asym(s, UP_GAIN, UP_CAP, DOWN_GAIN, DOWN_CAP)
    st["base"] = _clamp(st, st["base"] + step)
    st["trend_adj"] = _asym(st["fast"] - st["slow"], TREND_UP_GAIN, TREND_UP_CAP,
                            TREND_DOWN_GAIN, TREND_DOWN_CAP)
    st["limit"] = _clamp(st, st["base"] + st["trend_adj"])
    st["last_err"] = e
    st["last_signal"] = s
    st["last_step"] = step
    st["last_capped"] = 0
    return st


class Controller:
    def __init__(self, config: dict):
        st = {
            "min_limit": float(config.get("min_limit", 1.0)),
            "max_limit": float(config.get("max_limit", 200.0)),
        }
        if st["max_limit"] < st["min_limit"]:
            st["max_limit"] = st["min_limit"]
        st["base"] = _clamp(st, float(config.get("initial_limit", 50.0)))
        st["limit"] = st["base"]
        st["setpoint"] = None
        st.update(_HISTORY_DEFAULTS)
        st["last_err"] = 0.0
        st["last_signal"] = 0.0
        st["last_step"] = 0.0
        st["last_capped"] = 0
        self._st = st

    @property
    def limit(self) -> float:
        return self._st["limit"]

    def tick(self, obs: dict) -> dict:
        st = self._st
        sp = float(obs.get("setpoint_ms", 0.0))
        lat = float(obs.get("latency_ms", 0.0))
        arr = float(obs.get("arrival_rps", 0.0))
        if not math.isfinite(sp) or sp <= 0.0:
            sp = st["setpoint"] if st["setpoint"] is not None else 1.0
        if not math.isfinite(lat) or lat < 0.0:
            lat = sp
        if not math.isfinite(arr) or arr < 0.0:
            arr = 0.0

        if st["setpoint"] is not None and sp != st["setpoint"]:
            # Keep the current limit, forget everything learned before.
            st = dict(st)
            st["base"] = st["limit"]
            st.update(_HISTORY_DEFAULTS)
        st = dict(st)
        st["setpoint"] = sp

        e = min(max((lat - sp) / sp, -ERR_CLIP), ERR_CLIP)
        new = _advance(st, e, arr)
        if e < 0.0:
            # Cap the upward move relative to the downward move this same state
            # would make for the mirrored deviation.
            up = new["limit"] - st["limit"]
            down = st["limit"] - _advance(st, -e, arr)["limit"]
            cap = max(down, 0.0) / MIRROR_RATIO
            if up > cap:
                shift = up - cap
                new["limit"] = st["limit"] + cap
                new["base"] = _clamp(new, new["base"] - shift)
                new["last_capped"] = 1
        self._st = new
        return {"limit": new["limit"], "telemetry": self._telemetry()}

    def _telemetry(self) -> dict:
        st = self._st
        return {
            "limit": float(st["limit"]),
            "base_limit": float(st["base"]),
            "trend_adj": float(st["trend_adj"]),
            "error": float(st["last_err"]),
            "trend_fast": float(st["fast"]),
            "trend_slow": float(st["slow"]),
            "volatility": float(st["vol"]),
            "signal": float(st["last_signal"]),
            "step": float(st["last_step"]),
            "up_capped": int(st["last_capped"]),
            "saturated": int(st["base"] >= st["max_limit"] or st["base"] <= st["min_limit"]),
            "setpoint_ms": float(st["setpoint"] if st["setpoint"] is not None else 0.0),
            "samples_since_reset": int(st["n"]),
        }

    def snapshot(self) -> str:
        return json.dumps({k: self._st[k] for k in _STATE_KEYS}, sort_keys=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        obj = cls.__new__(cls)
        obj._st = {k: state[k] for k in _STATE_KEYS}
        return obj
