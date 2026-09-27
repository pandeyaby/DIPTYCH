"""Adaptive concurrency controller.

The limit is an integrator driven by the relative latency error
``e = (setpoint - latency) / setpoint`` (positive = headroom).

* Asymmetry: every increment goes through ``_gain``, which scales negative
  values (latency too high) by ``DOWN_GAIN`` and positive values by ``UP_GAIN``,
  with DOWN_GAIN = 3 * UP_GAIN.
* Recency: besides the raw error the increment contains the change of an
  exponentially weighted mean of the error (a trend term), so recent
  observations weigh more than old ones.
* Volatility: an exponentially weighted std-dev of the error is subtracted from
  the error, so noisier workloads settle on a lower limit.
* Anti-windup: the limit is clamped and, while the clamp is actively holding
  it, the filter state is reset so the first response after saturation depends
  only on the error just observed.
* Setpoint change: the filter state is reset, so corrections depend only on
  observations made after the change.
"""

from __future__ import annotations

import json

UP_GAIN = 4.0
DOWN_GAIN = 3.0 * UP_GAIN
TREND_GAIN = 2.0
ALPHA = 0.3  # smoothing of the error mean / variance
VOL_GAIN = 0.5
ERR_CLIP = 1.0


def _gain(v: float) -> float:
    return UP_GAIN * v if v >= 0.0 else DOWN_GAIN * v


class Controller:
    def __init__(self, config: dict):
        self.min_limit = float(config["min_limit"])
        self.max_limit = float(config["max_limit"])
        self.initial_limit = float(config["initial_limit"])
        self.limit = min(max(self.initial_limit, self.min_limit), self.max_limit)
        self.setpoint = None  # last setpoint seen
        self.mean = 0.0  # EW mean of error
        self.var = 0.0  # EW variance of error
        self.n = 0  # observations since last reset
        self.resets = 0

    # ------------------------------------------------------------------
    def _reset_filters(self) -> None:
        self.mean = 0.0
        self.var = 0.0
        self.n = 0

    def tick(self, obs: dict) -> dict:
        sp = float(obs["setpoint_ms"])
        lat = float(obs["latency_ms"])
        if self.setpoint is not None and sp != self.setpoint:
            self._reset_filters()
            self.resets += 1
        self.setpoint = sp

        e = (sp - lat) / sp if sp > 0.0 else 0.0
        e = min(max(e, -ERR_CLIP), ERR_CLIP)

        sd = self.var ** 0.5
        e_eff = e - VOL_GAIN * sd

        new_mean = self.mean + ALPHA * (e - self.mean)
        delta = _gain(e_eff) + _gain(TREND_GAIN * (new_mean - self.mean))

        raw = self.limit + delta
        new_limit = min(max(raw, self.min_limit), self.max_limit)
        clamped = new_limit != raw

        if clamped and (
            (raw > new_limit and e_eff >= 0.0) or (raw < new_limit and e_eff <= 0.0)
        ):
            # Pinned at a bound while the error pushes further into it.
            self._reset_filters()
        else:
            d = e - self.mean
            self.var = (1.0 - ALPHA) * (self.var + ALPHA * d * d)
            self.mean = new_mean
            self.n += 1
        self.limit = new_limit

        return {
            "limit": self.limit,
            "telemetry": {
                "error": e,
                "error_mean": self.mean,
                "error_sd": sd,
                "delta": delta,
                "clamped": int(clamped),
                "samples": self.n,
                "setpoint_resets": self.resets,
            },
        }

    # ------------------------------------------------------------------
    def snapshot(self) -> str:
        return json.dumps(
            {
                "cfg": [self.min_limit, self.max_limit, self.initial_limit],
                "limit": self.limit,
                "setpoint": self.setpoint,
                "mean": self.mean,
                "var": self.var,
                "n": self.n,
                "resets": self.resets,
            }
        )

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        s = json.loads(blob)
        mn, mx, init = s["cfg"]
        c = cls({"min_limit": mn, "max_limit": mx, "initial_limit": init})
        c.limit = s["limit"]
        c.setpoint = s["setpoint"]
        c.mean = s["mean"]
        c.var = s["var"]
        c.n = s["n"]
        c.resets = s["resets"]
        return c
