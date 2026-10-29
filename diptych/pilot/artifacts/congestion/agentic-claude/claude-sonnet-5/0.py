"""Delay-aware AIMD congestion controller.

Reno-style additive-increase/multiplicative-decrease, extended with a
Vegas-style queueing-delay estimate used both to pace the window gently
while alone on the link and to tell congestive loss from loss that is not
caused by queue overflow (random drops, or a sustained loss rate imposed
upstream of the bottleneck queue):

- A per-flow backlog estimate ``cwnd * (1 - min_rtt / rtt)`` (packets of
  this flow's own window presently sitting in the bottleneck queue) is
  kept in a small target band during congestion avoidance (grow below the
  band, shrink above it, hold inside it) instead of growing until a loss
  occurs, which keeps self-induced queueing delay low while alone on the
  link, and lets slow start exit before it fills the buffer.
- Whether a *loss* counts as congestive, however, is judged by
  ``delay_ratio = 1 - min_rtt / rtt`` -- the fraction of the RTT that is
  queueing delay -- rather than by the same absolute backlog. Delay_ratio
  does not depend on a flow's own window size, so two flows of this
  algorithm sharing a bottleneck agree on whether the path is congested.
  Judging congestion from the absolute per-flow backlog instead would let
  a large window hide a proportionally tiny backlog from itself while a
  small window trips the same threshold immediately, entrenching whichever
  flow happened to get ahead rather than letting a real loss event pull
  both back towards a fair share together.
- Loss that arrives while ``delay_ratio`` is small is not blamed on queue
  overflow and does not trigger a multiplicative decrease, so an
  occasional random drop no longer halves the window the way plain Reno's
  does.
- A trailing window of loss rate independently flags *sustained* loss even
  when it is not accompanied by queueing (e.g. loss injected upstream of
  the queue), so persistent heavy loss still gets backed off from even
  though the delay signal alone would miss it. It is an unweighted average
  of each tick's own loss fraction rather than sum(lost)/sum(sent): loss
  from queue overflow is split between flows in proportion to how much
  each offered that tick, so lost/sent at a given tick is, absent
  path-level loss, the same for every flow regardless of its own window
  size. Weighting by each tick's traffic volume would let a larger flow's
  own higher volume dilute its measured loss fraction relative to a
  smaller flow's, breaking that symmetry and, with it, fair convergence
  between two flows of this algorithm.
- The "minimum" RTT used as the no-queueing baseline cannot just be the
  all-time minimum: holding the backlog estimate in a band strictly above
  zero means this flow's own queueing never spontaneously drains to zero
  again after the first RTT, so an all-time minimum could never be
  refreshed if the path's true minimum RTT ever increased (a slower path,
  not a congested one) and nothing would tell the two apart. Instead, a
  short, infrequent probe periodically caps the window down near its
  floor for a few round trips, long enough for the queue this flow
  controls to actually drain, and the RTT observed at the end of that
  probe replaces the baseline outright -- a real resynchronization, not a
  decaying filter that would just as easily be fooled by sustained
  self-induced queueing.
"""

from __future__ import annotations

import math
from collections import deque


class Controller:
    DECREASE = 0.5              # multiplicative decrease on a congestive loss event
    TIMEOUT_CWND = 1.0          # window after a timeout
    BACKLOG_LO = 1.5            # target band (packets) for voluntary delay-based pacing
    BACKLOG_HI = 3.0
    SS_EXIT_BACKLOG = 2.5       # backlog threshold checked during slow start to exit early
    RATIO_BETA = 0.12           # delay_ratio above this (on a loss tick) counts as congestive
    HIGH_LOSS_THRESH = 0.06     # trailing loss rate treated as congestive regardless of delay
    LOSS_WINDOW = 50            # ticks of history used for the trailing loss rate
    PROBE_INTERVAL = 100        # ticks between base-RTT probes
    PROBE_DURATION = 8          # ticks each probe holds the window down
    PROBE_CWND = 4.0            # window used while probing

    def __init__(self, config: dict):
        self.lo = float(config["min_cwnd"])
        self.hi = float(config["max_cwnd"])
        self.cwnd = float(config["initial_cwnd"])
        self.ssthresh = self.hi
        self.min_rtt = math.inf
        self.recover_until = -1
        self._win: deque[float] = deque(maxlen=self.LOSS_WINDOW)
        self._win_sum = 0.0
        self.next_probe = self.PROBE_INTERVAL
        self.probe_end = -1
        self.probe_min_rtt = math.inf
        self._pre_probe_cwnd = self.cwnd

    def _update_loss_window(self, sent: float, lost: float) -> float:
        ratio = lost / sent if sent > 0 else 0.0
        if len(self._win) == self._win.maxlen:
            self._win_sum -= self._win[0]
        self._win.append(ratio)
        self._win_sum += ratio
        return self._win_sum / len(self._win)

    def tick(self, obs: dict) -> dict:
        t = int(obs["t"])
        acked = float(obs["acked"])
        lost = float(obs["lost"])
        rtt = max(float(obs["rtt"]), 1e-9)
        timeout = bool(obs["timeout"])
        sent = acked + lost

        if rtt < self.min_rtt:
            self.min_rtt = rtt

        if timeout and self.probe_end >= 0:
            # Abandon the probe; a real timeout takes priority.
            self.cwnd = self._pre_probe_cwnd
            self.probe_end = -1
            self.next_probe = t + self.PROBE_INTERVAL

        if not timeout:
            if self.probe_end < 0 and t >= self.next_probe:
                self._pre_probe_cwnd = self.cwnd
                self.probe_end = t + self.PROBE_DURATION
                self.probe_min_rtt = rtt

            if self.probe_end >= 0:
                self.probe_min_rtt = min(self.probe_min_rtt, rtt)
                if t < self.probe_end:
                    return {
                        "cwnd": min(self.hi, max(self.lo, self.PROBE_CWND)),
                        "telemetry": {"cwnd": self.cwnd, "min_rtt": self.min_rtt, "state": "probing"},
                    }
                self.min_rtt = self.probe_min_rtt
                self.probe_end = -1
                self.next_probe = t + self.PROBE_INTERVAL
                self.cwnd = self._pre_probe_cwnd

        loss_rate = self._update_loss_window(sent, lost)
        delay_ratio = 1.0 - self.min_rtt / rtt
        backlog = self.cwnd * delay_ratio

        state = "ca"
        if timeout:
            self.ssthresh = max(self.cwnd * self.DECREASE, 2.0)
            self.cwnd = self.TIMEOUT_CWND
            self.recover_until = t + math.ceil(rtt)
            state = "timeout"
        else:
            congestive = lost > 0 and (delay_ratio > self.RATIO_BETA or loss_rate > self.HIGH_LOSS_THRESH)
            if congestive and t >= self.recover_until:
                self.ssthresh = max(self.cwnd * self.DECREASE, 2.0)
                self.cwnd = self.ssthresh
                # Align the cooldown to a fixed grid (rather than
                # t + ceil(rtt), which depends on exactly when *this* flow
                # happened to detect congestion) so that two flows of this
                # algorithm sharing a bottleneck, which otherwise see
                # almost identical congestion signals, back off on the
                # same tick instead of alternating -- an unsynchronized
                # cooldown lets whichever flow's window happens to be open
                # take repeated hits while the other is protected,
                # entrenching unfairness instead of converging.
                k = max(1, round(rtt))
                self.recover_until = ((t // k) + 1) * k
                state = "backoff"
            elif congestive:
                state = "recovering"
            elif self.cwnd < self.ssthresh:
                if backlog > self.SS_EXIT_BACKLOG:
                    self.ssthresh = self.cwnd
                    state = "ss_exit"
                else:
                    self.cwnd += acked
                    state = "slow_start"
            else:
                step = acked / self.cwnd
                if backlog > self.BACKLOG_HI:
                    self.cwnd -= step
                    state = "ca_decrease"
                elif backlog < self.BACKLOG_LO:
                    self.cwnd += step
                    state = "ca_increase"
                # else: inside the target band, hold cwnd steady

        self.cwnd = min(self.hi, max(self.lo, self.cwnd))
        return {
            "cwnd": self.cwnd,
            "telemetry": {
                "cwnd": self.cwnd,
                "ssthresh": self.ssthresh,
                "min_rtt": self.min_rtt,
                "backlog": backlog,
                "delay_ratio": delay_ratio,
                "loss_rate": loss_rate,
                "state": state,
            },
        }
