import json

class Controller:
    def __init__(self, config: dict):
        self.config = dict(config)
        self.limit = config.get('initial_limit', 50.0)
        self.min_limit = config.get('min_limit', 1.0)
        self.max_limit = config.get('max_limit', 200.0)
        
        # Control parameters
        self.k_up = 0.1
        self.k_down_mag = 0.25
        
        # State
        self.error_history = []
        self.max_history = 20
        self.last_setpoint = None
        self.saturated_ticks = 0
        self.latency_mean = 0.0
        self.latency_m2 = 0.0
        self.latency_count = 0
    
    def tick(self, obs: dict) -> dict:
        latency = obs['latency_ms']
        setpoint = obs['setpoint_ms']
        
        # Setpoint change: reset state
        if self.last_setpoint != setpoint:
            self.error_history = []
            self.saturated_ticks = 0
            self.latency_mean = 0.0
            self.latency_m2 = 0.0
            self.latency_count = 0
            self.last_setpoint = setpoint
        
        # Error
        error = setpoint - latency
        
        # Maintain error history
        self.error_history.append(error)
        if len(self.error_history) > self.max_history:
            self.error_history.pop(0)
        
        # Update latency variance (Welford with decay)
        self.latency_count += 1
        delta = latency - self.latency_mean
        self.latency_mean += delta / min(self.latency_count, 50)
        delta2 = latency - self.latency_mean
        self.latency_m2 += delta * delta2
        
        if self.latency_count > 1:
            variance = self.latency_m2 / min(self.latency_count - 1, 50)
        else:
            variance = 0.0
        
        # Weighted error: recent observations carry more weight
        if self.error_history:
            n = len(self.error_history)
            weights = [2.0 ** (i - n + 1) for i in range(n)]
            total_weight = sum(weights)
            weighted_error = sum(w * e for w, e in zip(weights, self.error_history)) / total_weight
        else:
            weighted_error = 0.0
        
        # Track saturation for anti-windup
        at_max = self.limit >= self.max_limit - 0.001
        if at_max:
            self.saturated_ticks += 1
        else:
            self.saturated_ticks = 0
        
        # Asymmetric control: downward correction > 2x upward
        if weighted_error >= 0:
            control_signal = self.k_up * weighted_error
        else:
            control_signal = self.k_down_mag * weighted_error
        
        # Anti-windup: bound upward response after saturation
        if self.saturated_ticks > 0 and error > 0:
            control_signal = min(control_signal, abs(error) * 0.1)
        
        # Volatility suppression: higher variance → more conservative
        volatility_penalty = 0.0
        if variance > 1.0:
            volatility_penalty = -0.02 * (variance - 1.0)
        
        # Update limit
        new_limit = self.limit + control_signal + volatility_penalty
        self.limit = max(self.min_limit, min(self.max_limit, new_limit))
        
        # Telemetry with stable schema
        telemetry = {
            'error': error,
            'weighted_error': weighted_error,
            'variance': variance,
            'saturated_ticks': self.saturated_ticks,
            'control_signal': control_signal,
            'volatility_penalty': volatility_penalty,
        }
        
        return {
            'limit': self.limit,
            'telemetry': telemetry,
        }
    
    def snapshot(self) -> str:
        state = {
            'config': self.config,
            'limit': self.limit,
            'error_history': self.error_history,
            'last_setpoint': self.last_setpoint,
            'saturated_ticks': self.saturated_ticks,
            'latency_mean': self.latency_mean,
            'latency_m2': self.latency_m2,
            'latency_count': self.latency_count,
        }
        return json.dumps(state)
    
    @classmethod
    def restore(cls, blob: str) -> 'Controller':
        state = json.loads(blob)
        ctrl = cls(state['config'])
        ctrl.limit = state['limit']
        ctrl.error_history = state['error_history']
        ctrl.last_setpoint = state['last_setpoint']
        ctrl.saturated_ticks = state['saturated_ticks']
        ctrl.latency_mean = state['latency_mean']
        ctrl.latency_m2 = state['latency_m2']
        ctrl.latency_count = state['latency_count']
        return ctrl
