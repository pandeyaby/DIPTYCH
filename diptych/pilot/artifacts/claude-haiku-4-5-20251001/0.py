import json
import math

class Controller:
    def __init__(self, config: dict):
        self.min_limit = float(config.get('min_limit', 1.0))
        self.max_limit = float(config.get('max_limit', 200.0))
        self.initial_limit = float(config.get('initial_limit', 50.0))
        self.limit = self.initial_limit
        
        # Control parameters
        self.ema_alpha = 0.6
        self.down_gain = 2.5
        self.up_gain = 1.0
        
        # State
        self.ema_error = None
        self.error_history = []
        self.prev_setpoint = None
        self.max_history = 20
        
    def tick(self, obs: dict) -> dict:
        latency = obs['latency_ms']
        setpoint = obs['setpoint_ms']
        
        # Reset history on setpoint change
        if self.prev_setpoint is not None and self.prev_setpoint != setpoint:
            self.ema_error = None
            self.error_history.clear()
        self.prev_setpoint = setpoint
        
        # Calculate error
        error = latency - setpoint
        
        # Update EMA error for trend recency
        if self.ema_error is None:
            self.ema_error = error
        else:
            self.ema_error = self.ema_alpha * error + (1.0 - self.ema_alpha) * self.ema_error
        
        # Update error history for variance calculation
        self.error_history.append(error)
        if len(self.error_history) > self.max_history:
            self.error_history.pop(0)
        
        # Calculate variance of recent errors
        if len(self.error_history) > 1:
            mean = sum(self.error_history) / len(self.error_history)
            variance = sum((e - mean) ** 2 for e in self.error_history) / len(self.error_history)
        else:
            variance = 0.0
        
        # Asymmetric control law
        if self.ema_error > 0:
            correction = -self.down_gain * self.ema_error
        else:
            correction = self.up_gain * abs(self.ema_error)
        
        # Volatility suppression for upward corrections
        if self.ema_error < 0 and variance > 0:
            std_dev = math.sqrt(variance)
            volatility_factor = 1.0 + (std_dev / max(setpoint, 1.0)) * 0.3
            correction /= volatility_factor
        
        # Apply correction and clamp
        new_limit = self.limit + correction
        self.limit = max(self.min_limit, min(self.max_limit, new_limit))
        
        # Build telemetry with consistent schema
        telemetry = {
            'limit': self.limit,
            'error': error,
            'ema_error': self.ema_error,
            'variance': variance,
            'correction': correction,
        }
        
        return {
            'limit': self.limit,
            'telemetry': telemetry
        }
    
    def snapshot(self) -> str:
        state = {
            'limit': self.limit,
            'ema_error': self.ema_error,
            'error_history': self.error_history,
            'prev_setpoint': self.prev_setpoint,
            'min_limit': self.min_limit,
            'max_limit': self.max_limit,
            'initial_limit': self.initial_limit,
            'ema_alpha': self.ema_alpha,
            'down_gain': self.down_gain,
            'up_gain': self.up_gain,
        }
        return json.dumps(state)
    
    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        config = {
            'min_limit': state['min_limit'],
            'max_limit': state['max_limit'],
            'initial_limit': state['initial_limit'],
        }
        ctrl = cls(config)
        ctrl.limit = state['limit']
        ctrl.ema_error = state['ema_error']
        ctrl.error_history = state['error_history']
        ctrl.prev_setpoint = state['prev_setpoint']
        ctrl.ema_alpha = state['ema_alpha']
        ctrl.down_gain = state['down_gain']
        ctrl.up_gain = state['up_gain']
        return ctrl
