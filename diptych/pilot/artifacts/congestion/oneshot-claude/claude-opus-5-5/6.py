import math


class Controller:
    """Delay-aware AIMD congestion controller.

    How it works:
      * Slow start doubles the window each round trip. It ends at the first
        congestion loss, or when queueing delay passes the target. On a delay
        exit the window is set to the measured delivery rate times min_rtt
        (about one BDP), so slow start does not fill the buffer.
      * Congestion avoidance adds one packet per RTT. Once per RTT
        (one-RTT cooldown) it multiplies the window by 0.85 if the average
        queueing delay exceeds a small target. Both flows see the same delay
        and react the same way, so two copies converge to a fair share. The
        queue stays short when the flow is alone.
      * A loss counts as congestion when it happens with a large queue
        (drop-tail overflow) or when the loss rate is high. Congestion losses
        halve the window, at most once per RTT, as Reno does. Losses at low
        queueing delay are treated as random and ignored, which keeps
        throughput high on lossy paths.
      * A timeout drops the window to min_cwnd.
      * If the minimum RTT rises (the path changed), the flow cuts its window
        but the RTT stays flat. When it sees this, it resets min_rtt and goes
        back to slow start.
    """

    def __init__(self, config: dict):
        self.min_cwnd = float(config.get("min_cwnd", 1.0))
        self.max_cwnd = float(config.get("max_cwnd", 2000.0))
        self.cwnd = float(config.get("initial_cwnd", 4.0))
        self.cwnd = self._clamp(self.cwnd)

        self.ssthresh = self.max_cwnd
        self.slow_start = True

        self.min_rtt = None
        self.srtt = None
        self.rtt_max = None
        self.buf_est = None  # queueing delay (ticks) observed at congestion losses

        # loss-rate EWMA (packet weighted)
        self.L = 0.0
        self.S = 0.0

        self.last_loss_cut = -1e18
        self.in_timeout = False

        # epoch (≈ one RTT) statistics
        self.cooldown = 0
        self.streak = 0
        self.ep_mins = []
        self._reset_epoch()

        self.mode = "slow_start"
        self.last_q = 0.0
        self.last_target = 0.0

    # ------------------------------------------------------------------ utils
    def _clamp(self, w):
        if w != w or w is None:  # NaN guard
            w = self.min_cwnd
        return max(self.min_cwnd, min(self.max_cwnd, w))

    def _reset_epoch(self):
        self.ep_ticks = 0
        self.ep_n = 0
        self.ep_sum = 0.0
        self.ep_min = float("inf")
        self.ep_acked = 0.0

    def _target(self):
        tgt = 0.1 * self.min_rtt
        if self.buf_est is not None and self.buf_est > 0:
            tgt = min(tgt, 0.3 * self.buf_est)
        return max(tgt, 1e-6)

    def _out(self, lr):
        self.cwnd = self._clamp(self.cwnd)
        return {
            "cwnd": float(self.cwnd),
            "telemetry": {
                "mode": self.mode,
                "min_rtt": float(self.min_rtt if self.min_rtt is not None else -1.0),
                "srtt": float(self.srtt if self.srtt is not None else -1.0),
                "rtt_max": float(self.rtt_max if self.rtt_max is not None else -1.0),
                "buf_est": float(self.buf_est if self.buf_est is not None else -1.0),
                "target": float(self.last_target),
                "qdelay": float(self.last_q),
                "loss_rate": float(lr),
                "ssthresh": float(self.ssthresh),
                "slow_start": int(self.slow_start),
            },
        }

    # ------------------------------------------------------------------- tick
    def tick(self, obs: dict) -> dict:
        t = int(obs.get("t", 0))
        acked = float(obs.get("acked", 0.0) or 0.0)
        lost = float(obs.get("lost", 0.0) or 0.0)
        rtt = obs.get("rtt", 0.0)
        try:
            rtt = float(rtt)
        except (TypeError, ValueError):
            rtt = 0.0
        timeout = bool(obs.get("timeout", False))

        if acked < 0 or acked != acked:
            acked = 0.0
        if lost < 0 or lost != lost:
            lost = 0.0

        # loss-rate estimate
        self.L = 0.99 * self.L + lost
        self.S = 0.99 * self.S + acked + lost
        lr = self.L / self.S if self.S > 50.0 else 0.0

        valid = (not timeout) and acked > 0 and rtt > 0 and math.isfinite(rtt)

        if valid:
            if self.min_rtt is None or rtt < self.min_rtt:
                self.min_rtt = rtt
            if self.srtt is None:
                self.srtt = rtt
            else:
                self.srtt = 0.875 * self.srtt + 0.125 * rtt
            if self.rtt_max is None or rtt > self.rtt_max:
                self.rtt_max = rtt

        # ---------------------------------------------------------- timeout
        if timeout:
            if not self.in_timeout:
                self.ssthresh = max(2.0, self.cwnd / 2.0)
            self.in_timeout = True
            self.cwnd = self.min_cwnd
            self.slow_start = True
            self.cooldown = 0
            self.streak = 0
            self.ep_mins = []
            self._reset_epoch()
            self.mode = "timeout"
            return self._out(lr)
        self.in_timeout = False

        if self.min_rtt is None:
            # No RTT information yet; grow cautiously as in slow start.
            if acked > 0:
                self.cwnd += acked
            return self._out(lr)

        srtt = self.srtt if self.srtt is not None else self.min_rtt
        target = self._target()
        self.last_target = target
        cur_rtt = rtt if valid else srtt
        q_now = max(0.0, cur_rtt - self.min_rtt)
        self.last_q = q_now

        # ------------------------------------------------------------- loss
        cut_this_tick = False
        if lost > 0:
            qmax = max(0.0, (self.rtt_max if self.rtt_max is not None else cur_rtt)
                       - self.min_rtt)
            high_loss = lr > 0.05
            overflow = q_now >= max(0.5 * qmax, 2.0 * target) and q_now > 0
            if high_loss or overflow:
                if overflow and not high_loss:
                    if self.buf_est is None:
                        self.buf_est = q_now
                    else:
                        self.buf_est = 0.5 * self.buf_est + 0.5 * q_now
                if t - self.last_loss_cut >= max(1.0, srtt):
                    self.cwnd = self._clamp(self.cwnd * 0.5)
                    self.ssthresh = max(2.0, self.cwnd)
                    self.slow_start = False
                    self.last_loss_cut = t
                    self.cooldown = 1
                    self.streak = 0
                    self.ep_mins = []
                    self._reset_epoch()
                    cut_this_tick = True
                    self.mode = "loss_cut"

        # --------------------------------------------------------- increase
        if not cut_this_tick and acked > 0:
            if self.slow_start:
                self.cwnd += acked
                if self.cwnd >= self.ssthresh:
                    self.cwnd = min(self.cwnd, max(self.ssthresh, self.min_cwnd))
                    self.slow_start = False
            else:
                self.cwnd += acked / max(self.cwnd, 1.0)

        # ------------------------------------------------------------ epoch
        if not cut_this_tick:
            self.ep_ticks += 1
            self.ep_acked += acked
            if valid:
                self.ep_n += 1
                self.ep_sum += rtt
                if rtt < self.ep_min:
                    self.ep_min = rtt

            if self.ep_ticks >= max(1.0, srtt):
                if self.ep_n > 0:
                    self._end_epoch(target)
                self._reset_epoch()

        if not self.slow_start and self.mode == "slow_start":
            self.mode = "avoid"
        return self._out(lr)

    # -------------------------------------------------------------- epoch end
    def _end_epoch(self, target):
        avg = self.ep_sum / self.ep_n
        q_avg = max(0.0, avg - self.min_rtt)
        q_min = max(0.0, self.ep_min - self.min_rtt)
        rate = self.ep_acked / max(1, self.ep_ticks)

        # Base-RTT change detection: the queue stays up but the RTT does not
        # move even though the window was cut.
        if q_min > target:
            self.streak += 1
            self.ep_mins.append(self.ep_min)
            if len(self.ep_mins) > 3:
                self.ep_mins.pop(0)
        else:
            self.streak = 0
            self.ep_mins = []

        if self.streak >= 4 and len(self.ep_mins) == 3:
            spread = max(self.ep_mins) - min(self.ep_mins)
            if spread <= 0.02 * self.min_rtt + 1e-9:
                new_min = self.ep_mins[-1]
                old_qmax = max(0.0, (self.rtt_max or new_min) - self.min_rtt)
                self.rtt_max = max(self.rtt_max or new_min, new_min + 0.5 * old_qmax)
                self.min_rtt = new_min
                self.streak = 0
                self.ep_mins = []
                self.cooldown = 0
                if self.cwnd >= 16.0:
                    self.slow_start = True
                    self.ssthresh = self.max_cwnd
                self.mode = "min_reset"
                return

        if self.cooldown > 0:
            self.cooldown -= 1
            return

        if self.slow_start:
            if q_avg > target:
                bdp_est = rate * self.min_rtt
                new_w = min(self.cwnd, max(bdp_est, 0.5 * self.cwnd))
                self.cwnd = self._clamp(new_w)
                self.ssthresh = max(2.0, self.cwnd)
                self.slow_start = False
                self.cooldown = 1
                self.mode = "delay_exit"
        else:
            if q_avg > target:
                self.cwnd = self._clamp(self.cwnd * 0.85)
                self.cooldown = 1
                self.mode = "delay_cut"
            else:
                self.mode = "avoid"
