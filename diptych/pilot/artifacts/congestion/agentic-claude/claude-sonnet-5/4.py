"""Delay-guided congestion control with a loss-rate safety net.

Congestion avoidance follows a FAST-TCP-style rule: track the minimum RTT
seen in a recent sliding window as an estimate of the path's base delay, and
each tick nudge ``cwnd`` toward ``cwnd * min_rtt / rtt + alpha`` -- a target
that keeps a small, roughly constant amount of self-induced queueing
(``alpha`` packets) regardless of the path's capacity or base RTT. Because
the rule is expressed as a ratio of RTTs rather than absolute values, it
re-centers automatically when the path's base RTT or capacity changes.

Isolated, non-congestive loss (a few percent, uncorrelated with queueing)
is ignored, since the delay signal is what drives the window -- this is what
lets the controller hold throughput on a lossy path where AIMD would
collapse. A smoothed loss-rate estimate is kept as a separate safety net:
once it is high enough to signal real, sustained congestion or path damage
rather than noise, it multiplicatively cuts the window, with harsher cuts
the higher the sustained rate climbs. A timeout (or a loss event in slow
start) ends slow start and a timeout always forces a hard, full backoff.
"""

from __future__ import annotations

from collections import deque


class Controller:
    WIN = 150                 # ticks kept in the min-RTT sliding window
    ALPHA = 2.0                # target self-induced queueing, in packets
    GAMMA = 0.25                # FAST-style per-RTT correction fraction
    LOSS_EWMA_ALPHA = 0.15      # smoothing for the loss-rate estimate
    LOSS_LOW = 0.03             # loss rates at/below this are treated as noise
    LOSS_HIGH = 0.08            # loss rates at/above this get the full cut
    LOSS_CUT = 0.6               # fraction shaved off at full severity
    HYSTART_RATIO = 1.5         # exit slow start once rtt grows this far past min_rtt
    SS_BACKOFF = 1.2            # cwnd shrink applied when leaving slow start on delay

    def __init__(self, config: dict):
        self.lo = float(config["min_cwnd"])
        self.hi = float(config["max_cwnd"])
        self.cwnd = float(config["initial_cwnd"])
        self.ssthresh = self.hi
        self.slow_start = True
        self.rtt_win: deque = deque()
        self.loss_ewma = 0.0
        self.recover_until = -1

    def _min_rtt(self, t: int, rtt: float) -> float:
        dq = self.rtt_win
        while dq and dq[-1][1] >= rtt:
            dq.pop()
        dq.append((t, rtt))
        while dq[0][0] <= t - self.WIN:
            dq.popleft()
        return dq[0][1]

    def tick(self, obs: dict) -> dict:
        t = int(obs["t"])
        acked = float(obs["acked"])
        lost = float(obs["lost"])
        rtt = float(obs["rtt"]) if obs["rtt"] > 0 else 1.0
        timeout = bool(obs["timeout"])

        sent = acked + lost
        inst_loss = lost / sent if sent > 0 else 0.0
        self.loss_ewma = (1 - self.LOSS_EWMA_ALPHA) * self.loss_ewma + self.LOSS_EWMA_ALPHA * inst_loss

        min_rtt = self._min_rtt(t, rtt)

        if timeout:
            self.cwnd = max(self.lo, min(2.0, self.cwnd * 0.5))
            self.ssthresh = max(self.cwnd, 2.0 * self.lo)
            self.slow_start = True
            self.recover_until = t + 1
        elif self.slow_start:
            if lost > 0 or rtt > min_rtt * self.HYSTART_RATIO:
                self.slow_start = False
                self.ssthresh = self.cwnd
                self.cwnd = max(self.lo, self.cwnd / self.SS_BACKOFF)
            else:
                self.cwnd += acked
        else:
            target = self.cwnd * (min_rtt / rtt) + self.ALPHA
            step = self.GAMMA / max(1.0, rtt)
            self.cwnd += step * (target - self.cwnd)

            if self.loss_ewma > self.LOSS_LOW and t >= self.recover_until:
                severity = min(1.0, (self.loss_ewma - self.LOSS_LOW) / (self.LOSS_HIGH - self.LOSS_LOW))
                factor = 1.0 - self.LOSS_CUT * severity
                self.cwnd *= factor
                self.recover_until = t + max(1, round(rtt))

        self.cwnd = min(self.hi, max(self.lo, self.cwnd))
        telemetry = {
            "cwnd": self.cwnd,
            "min_rtt": min_rtt,
            "loss_ewma": self.loss_ewma,
            "slow_start": int(self.slow_start),
        }
        return {"cwnd": self.cwnd, "telemetry": telemetry}
