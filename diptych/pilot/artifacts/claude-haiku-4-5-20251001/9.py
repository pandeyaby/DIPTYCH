import json
from collections import deque
from math import sqrt


class Controller:
    def __init__(self, config: dict):
        self.config = config.copy()
        self.min_limit = config.get('min_limit', 1.0)
        self.max_limit = config.get('max_limit', 200.0)
        self.limit = config.get('initial_limit', 50.0)
        
        self.t = 0
        self.current_setpoint = None
        self.error_history = deque(maxlen=100)
        self.saturation_ticks = 0
        
        self.k_up = 0.008
        self.k_down = 0.020
    
    def _calculate_std_dev(self):
        if not self.error_history:
            return 0.0
        mean_e = sum(self.error_history) / len(self.error_history)
        variance = sum((e - mean_e) ** 2 for e in self.error_history) / len(self.error_history)
        return sqrt(variance)
    
    def _calculate_trend(self, setpoint):
        if len(self.error_history) < 10:
            return 0.0
        recent = list(self.error_history)[-5:]
        old = list(self.error_history)[:-5]
        recent_mean = sum(recent) / len(recent)
        old_mean = sum(old) / len(old)
        return (old_mean - recent_mean) / max(setpoint, 0.1)
    
    def tick(self, obs: dict) -> dict:
        t = obs['t']
        latency = obs['latency_ms']
        setpoint = obs['setpoint_ms']
        arrival_rps = obs['arrival_rps']
        admitted_rps = obs['admitted_rps']
        
        if self.current_setpoint != setpoint:
            self.current_setpoint = setpoint
            self.error_history.clear()
            self.saturation_ticks = 0
        
        error = latency - setpoint
        self.error_history.append(error)
        
        at_max = self.limit >= self.max_limit * 0.9999
        if at_max:
            self.saturation_ticks += 1
        else:
            self.saturation_ticks = 0
        
        std_dev = self._calculate_std_dev()
        trend = self._calculate_trend(setpoint)
        
        vol_factor = 1.0 + (std_dev / max(setpoint, 0.1)) * 0.3
        trend_factor = 1.0 + trend * 0.05
        
        if error > 0:
            adjustment = 1.0 - self.k_down * error * vol_factor
            if self.saturation_ticks == 1:
                min_adj = 1.0 - self.k_down * error * 2.0
                adjustment = max(min_adj, adjustment)
        else:
            adjustment = 1.0 - self.k_up * error * vol_factor
        
        adjustment = adjustment * trend_factor
        
        new_limit = self.limit * adjustment
        self.limit = max(self.min_limit, min(self.max_limit, new_limit))
        
        telemetry = {
            'limit': self.limit,
            'error': error,
            'saturation_ticks': self.saturation_ticks,
            'std_dev': std_dev,
            'trend': trend,
            'vol_factor': vol_factor,
        }
        
        self.t = t
        
        return {
            'limit': self.limit,
            'telemetry': telemetry
        }
    
    def snapshot(self) -> str:
        state = {
            'config': self.config,
            'limit': self.limit,
            't': self.t,
            'current_setpoint': self.current_setpoint,
            'error_history': list(self.error_history),
            'saturation_ticks': self.saturation_ticks,
        }
        return json.dumps(state, separators=(',', ':'), sort_keys=True)
    
    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        controller = cls(state['config'])
        controller.limit = state['limit']
        controller.t = state['t']
        controller.current_setpoint = state['current_setpoint']
        controller.error_history = deque(state['error_history'], maxlen=100)
        controller.saturation_ticks = state['saturation_ticks']
        return controller
