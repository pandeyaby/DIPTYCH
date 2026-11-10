import math
import json

class Controller:
    def __init__(self, config: dict):
        self.min_limit = float(config['min_limit'])
        self.max_limit = float(config['max_limit'])
        self.limit = float(config['initial_limit'])
        
        self.a_down = 0.3
        self.a_up = 0.1
        self.alpha = 0.1
        self.variance_threshold = 1.0
        self.variance_factor = 0.8
        
        self.last_setpoint = None
        self.errors = []
        self.latencies = []
        self.was_saturated = False
        
    def tick(self, obs: dict) -> dict:
        t = obs['t']
        latency = obs['latency_ms']
        setpoint = obs['setpoint_ms']
        
        if self.last_setpoint is not None and self.last_setpoint != setpoint:
            self.errors = []
            self.latencies = []
        
        self.last_setpoint = setpoint
        
        error = latency - setpoint
        self.errors.append((t, error))
        self.latencies.append((t, latency))
        
        if len(self.errors) > 100:
            self.errors = self.errors[-100:]
        if len(self.latencies) > 100:
            self.latencies = self.latencies[-100:]
        
        weighted_error = self._weighted_error(t)
        variance = self._variance()
        is_saturated = self.limit >= self.max_limit * 0.99
        
        adjustment = self._adjust(weighted_error, variance, self.was_saturated)
        
        new_limit = self.limit + adjustment
        new_limit = max(self.min_limit, min(self.max_limit, new_limit))
        self.limit = new_limit
        
        self.was_saturated = is_saturated
        
        telemetry = {
            'adjustment': adjustment,
            'error': error,
            'limit': new_limit,
            'variance': variance,
            'weighted_error': weighted_error,
        }
        
        return {'limit': new_limit, 'telemetry': telemetry}
    
    def _weighted_error(self, t):
        if not self.errors:
            return 0.0
        
        weighted_sum = 0.0
        weight_sum = 0.0
        
        for obs_t, err in self.errors:
            weight = math.exp(-self.alpha * (t - obs_t))
            weighted_sum += err * weight
            weight_sum += weight
        
        return weighted_sum / weight_sum if weight_sum > 0 else 0.0
    
    def _variance(self):
        if len(self.latencies) < 2:
            return 0.0
        
        recent = [lat for _, lat in self.latencies[-20:]]
        mean = sum(recent) / len(recent)
        return sum((x - mean) ** 2 for x in recent) / len(recent)
    
    def _adjust(self, weighted_error, variance, was_saturated):
        adjustment = -self.a_down * weighted_error if weighted_error > 0 else -self.a_up * weighted_error
        
        if variance > self.variance_threshold:
            adjustment *= self.variance_factor
        
        if was_saturated and self.errors:
            current_error = self.errors[-1][1]
            max_adj = abs(current_error)
            adjustment = max(-max_adj, min(max_adj, adjustment))
        
        return adjustment
    
    def snapshot(self) -> str:
        state = {
            'a_down': self.a_down,
            'a_up': self.a_up,
            'alpha': self.alpha,
            'errors': [[t, e] for t, e in self.errors],
            'latencies': [[t, l] for t, l in self.latencies],
            'last_setpoint': self.last_setpoint,
            'limit': self.limit,
            'max_limit': self.max_limit,
            'min_limit': self.min_limit,
            'variance_factor': self.variance_factor,
            'variance_threshold': self.variance_threshold,
            'was_saturated': self.was_saturated,
        }
        return json.dumps(state, sort_keys=True, separators=(',', ':'))
    
    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        ctrl = cls({
            'min_limit': state['min_limit'],
            'max_limit': state['max_limit'],
            'initial_limit': state['limit'],
        })
        ctrl.limit = state['limit']
        ctrl.a_down = state['a_down']
        ctrl.a_up = state['a_up']
        ctrl.alpha = state['alpha']
        ctrl.variance_threshold = state['variance_threshold']
        ctrl.variance_factor = state['variance_factor']
        ctrl.last_setpoint = state['last_setpoint']
        ctrl.errors = [(int(t), float(e)) for t, e in state['errors']]
        ctrl.latencies = [(int(t), float(l)) for t, l in state['latencies']]
        ctrl.was_saturated = state['was_saturated']
        return ctrl
