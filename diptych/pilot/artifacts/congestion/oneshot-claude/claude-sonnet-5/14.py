"""Congestion control with delay-aware slow start/CA and loss-rate-gated AIMD.

The design separates "random" loss (low, roughly-steady loss rate) from
congestion loss (loss rate climbing well above noise level) using an EWMA of
the per-tick loss fraction, and additionally watches queueing delay
(rtt - estimated base rtt) so the flow backs off *before* the bottleneck
queue fills and before packets are lost. This keeps queueing low when alone
on the link, tolerates random loss without needless halving, and yields
bandwidth promptly to other flows (helping fairness/latency/short-flow
behaviour) while still recovering a full window on sustained heavy loss or a
timeout.
"""


class Controller:
    def __init__(self, config: dict):
        self.min_cwnd = float(config.get("min_cwnd", 1.0))
        self.max_cwnd = float(config.get("max_cwnd", 2000.0))
        self.cwnd = self._clamp(float(config.get("initial_cwnd", 4.0)))

        self.in_slow_start = True
        self.ssthresh = self.max_cwnd

        # Base (no-queue) RTT estimate. Never-lower-than-seen, but re-probed
        # periodically so a real increase in base RTT (e.g. path change) is
        # eventually recognized instead of looking like permanent queueing.
        self.min_rtt = float("inf")
        self.min_rtt_tick = 0
        self.reset_interval = 250.0

        self.loss_ewma = 0.0
        self.last_decrease_tick = -1.0e18

    def _clamp(self, cwnd: float) -> float:
        return max(self.min_cwnd, min(self.max_cwnd, cwnd))

    def tick(self, obs: dict) -> dict:
        t = float(obs["t"])
        rtt = float(obs["rtt"])
        acked = float(obs["acked"])
        lost = float(obs["lost"])
        timeout = bool(obs["timeout"])

        if timeout:
            self.cwnd = self.min_cwnd
            self.in_slow_start = True
            self.ssthresh = self.max_cwnd
            self.loss_ewma = 0.0
            self.last_decrease_tick = t
            cwnd = self._clamp(self.cwnd)
            self.cwnd = cwnd
            return {
                "cwnd": cwnd,
                "telemetry": {"event": "timeout", "loss_ewma": self.loss_ewma},
            }

        if rtt < self.min_rtt:
            self.min_rtt = rtt
            self.min_rtt_tick = t
        elif t - self.min_rtt_tick > self.reset_interval:
            self.min_rtt = rtt
            self.min_rtt_tick = t

        sent = acked + lost
        if sent > 0.0:
            frac = lost / sent
            self.loss_ewma = 0.8 * self.loss_ewma + 0.2 * frac

        rtt_gate = max(1.0, rtt)
        decreased = False

        # Congestion-level loss (well above steady random-loss noise):
        # halve, at most once per round trip.
        if lost > 0.0 and self.loss_ewma > 0.05 and (t - self.last_decrease_tick) >= rtt_gate:
            self.cwnd = max(self.min_cwnd, self.cwnd * 0.5)
            self.last_decrease_tick = t
            self.in_slow_start = False
            self.ssthresh = self.cwnd
            decreased = True

        base = self.min_rtt if self.min_rtt != float("inf") else rtt
        queue_delay = max(0.0, rtt - base)
        soft_threshold = max(1.0, 0.4 * base)
        severe_threshold = max(2.0 * soft_threshold, soft_threshold + 1.0)

        if not decreased:
            if queue_delay > severe_threshold and (t - self.last_decrease_tick) >= rtt_gate:
                # Proactive, gentle cut: queue is building badly even without
                # loss yet, so back off before it spills over.
                self.cwnd = max(self.min_cwnd, self.cwnd * 0.9)
                self.last_decrease_tick = t
                self.in_slow_start = False
                self.ssthresh = self.cwnd
            elif self.in_slow_start:
                if queue_delay > soft_threshold:
                    self.in_slow_start = False
                    self.ssthresh = self.cwnd
                    self.cwnd += acked / self.cwnd
                else:
                    self.cwnd += acked
                    if self.cwnd >= self.ssthresh:
                        self.in_slow_start = False
            else:
                if queue_delay <= soft_threshold:
                    self.cwnd += acked / self.cwnd
                # else: hold steady, let the queue drain.

        cwnd = self._clamp(self.cwnd)
        self.cwnd = cwnd

        telemetry = {
            "min_rtt": base,
            "queue_delay": queue_delay,
            "loss_ewma": self.loss_ewma,
            "slow_start": 1 if self.in_slow_start else 0,
            "phase": "slow_start" if self.in_slow_start else "congestion_avoidance",
        }
        return {"cwnd": cwnd, "telemetry": telemetry}
