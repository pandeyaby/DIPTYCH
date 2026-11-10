class Controller:
    def __init__(self, config: dict):
        self.min_cwnd = config.get('min_cwnd', 1.0)
        self.max_cwnd = config.get('max_cwnd', 2000.0)
        self.cwnd = config.get('initial_cwnd', 4.0)
        self.ssthresh = self.max_cwnd
        self.last_loss_time = -float('inf')
        self.min_rtt = float('inf')
        self.loss_history = []
        self.acked_history = []

    def tick(self, obs: dict) -> dict:
        t = obs['t']
        acked = obs['acked']
        lost = obs['lost']
        rtt = obs['rtt']
        timeout = obs['timeout']

        self.loss_history.append((t, lost))
        self.acked_history.append((t, acked))

        cutoff_time = t - 100
        self.loss_history = [(time, loss) for time, loss in self.loss_history if time > cutoff_time]
        self.acked_history = [(time, ack) for time, ack in self.acked_history if time > cutoff_time]

        total_lost = sum(loss for _, loss in self.loss_history)
        total_acked = sum(ack for _, ack in self.acked_history)
        total_sent = total_lost + total_acked
        loss_rate = total_lost / total_sent if total_sent > 0 else 0

        self.min_rtt = min(self.min_rtt, rtt)
        rtt_inflation = (rtt - self.min_rtt) / self.min_rtt if self.min_rtt > 0 else 0

        if timeout:
            self.cwnd = self.min_cwnd
            self.ssthresh = self.min_cwnd
            self.last_loss_time = t
        elif lost > 0 and t - self.last_loss_time >= rtt:
            if loss_rate > 0.1:
                self.cwnd *= 0.3
            elif loss_rate > 0.05:
                self.cwnd *= 0.5
            elif rtt_inflation > 0.1:
                self.cwnd *= 0.5
            else:
                self.cwnd *= 0.95

            self.cwnd = max(self.min_cwnd, self.cwnd)
            self.ssthresh = self.cwnd
            self.last_loss_time = t
        elif not timeout and lost == 0:
            if rtt_inflation > 0.3:
                if self.cwnd < self.ssthresh:
                    self.ssthresh = self.cwnd
                self.cwnd += acked / (2.0 * self.cwnd)
            elif self.cwnd < self.ssthresh:
                self.cwnd += acked
            else:
                self.cwnd += acked / self.cwnd

        self.cwnd = max(self.min_cwnd, min(self.max_cwnd, self.cwnd))

        return {
            'cwnd': self.cwnd,
            'telemetry': {
                'cwnd': self.cwnd,
                'loss_rate': loss_rate,
                'rtt_inflation': rtt_inflation,
            }
        }
