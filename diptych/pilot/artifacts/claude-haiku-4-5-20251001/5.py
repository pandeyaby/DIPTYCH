import json
from collections import deque
from statistics import variance as calc_variance


class Controller:
    def __init__(self, config: dict):
        self.min_limit = float(config.get('min_limit', 1.0))
        self.max_limit = float(config.get('max_limit', 200.0))
        self.initial_limit = float(config.get('initial_limit', 50.0))
        self.limit = self.initial_limit
        
        # Control parameters (k_down / k_up = 2.5 > 2 satisfies asymmetric response)
        self.k_down = 0.25
        self.k_up = 0.1
        self.ema_alpha = 0.3
        
        # State
        self.error_ema = 0.0
        self.error_history = deque(maxlen=20)
        self.last_setpoint = None
        self.last_at_max = False
        
    def tick(self, obs: dict) -> dict:
        latency = obs['latency_ms']
        setpoint = obs['setpoint_ms']
        
        # Reset history on setpoint change (setpoint-change quiet)
        if self.last_setpoint is not None and self.last_setpoint != setpoint:
            self.error_ema = 0.0
            self.error_history.clear()
        
        self.last_setpoint = setpoint
        
        # Calculate error
        error = latency - setpoint
        self.error_history.append(error)
        
        # Update EMA for trend (trend recency: recent observations weighted more)
        if len(self.error_history) == 1:
            self.error_ema = error
        else:
            self.error_ema = self.ema_alpha * error + (1 - self.ema_alpha) * self.error_ema
        
        # Calculate variance for volatility suppression
        if len(self.error_history) > 1:
            error_var = calc_variance(self.error_history)
        else:
            error_var = 0.0
        
        # Calculate adjustment with asymmetric response
        if error >= 0:
            # Latency too high: reduce limit aggressively
            adj = -self.k_down * max(0, error) / max(setpoint, 1.0)
        else:
            # Latency too low: increase limit conservatively
            adj = self.k_up * abs(error) / max(setpoint, 1.0)
            
            # Anti-windup: reduce aggressiveness if just leaving saturation
            if self.last_at_max:
                adj *= 0.3
            
            # Volatility suppression: penalize high variance
            if error_var > 0:
                vol_factor = max(0.5, 1.0 - error_var / max(setpoint ** 2, 1.0))
                adj *= vol_factor
        
        # Apply adjustment and clamp to bounds
        new_limit = self.limit * (1.0 + adj)
        self.limit = max(self.min_limit, min(self.max_limit, new_limit))
        
        # Track saturation for anti-windup
        at_max = self.limit >= self.max_limit * 0.999999
        self.last_at_max = at_max
        
        # Telemetry with stable schema (same keys every tick)
        telemetry = {
            'limit': self.limit,
            'error': error,
            'error_ema': self.error_ema,
            'error_variance': error_var,
            'adjustment': adj,
            'saturated': int(at_max),
        }
        
        return {
            'limit': self.limit,
            'telemetry': telemetry,
        }
    
    def snapshot(self) -> str:
        state = {
            'limit': self.limit,
            'error_ema': self.error_ema,
            'error_history': list(self.error_history),
            'last_setpoint': self.last_setpoint,
            'last_at_max': self.last_at_max,
            'k_down': self.k_down,
            'k_up': self.k_up,
            'ema_alpha': self.ema_alpha,
        }
        return json.dumps(state)
    
    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        ctrl = cls({'min_limit': 1.0, 'max_limit': 200.0})
        ctrl.limit = state['limit']
        ctrl.error_ema = state['error_ema']
        ctrl.error_history = deque(state['error_history'], maxlen=20)
        ctrl.last_setpoint = state['last_setpoint']
        ctrl.last_at_max = state['last_at_max']
        ctrl.k_down = state['k_down']
        ctrl.k_up = state['k_up']
        ctrl.ema_alpha = state['ema_alpha']
        return ctrl
