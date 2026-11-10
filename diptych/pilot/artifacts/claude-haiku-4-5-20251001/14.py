import json

class Controller:
    def __init__(self, config: dict):
        self.min_limit = float(config.get('min_limit', 1.0))
        self.max_limit = float(config.get('max_limit', 200.0))
        self.initial_limit = float(config.get('initial_limit', 50.0))
        
        self.current_limit = max(self.min_limit, min(self.max_limit, self.initial_limit))
        
        # EMA state for trend recency
        self.ema_latency = None
        self.ema_variance = None
        self.ema_alpha = 0.25
        
        # Integral state for smooth correction
        self.integral_error = 0.0
        self.i_gain = 0.05
        
        # Asymmetric proportional gains (downward > 2x upward)
        self.p_gain_down = 0.4
        self.p_gain_up = 0.15
        
        # Saturation tracking for anti-windup
        self.was_saturated_max = False
        
        # Setpoint tracking
        self.last_setpoint = None
    
    def tick(self, obs: dict) -> dict:
        latency = obs['latency_ms']
        setpoint = obs['setpoint_ms']
        
        # Reset on setpoint change (setpoint-change quiet)
        if self.last_setpoint != setpoint:
            self.ema_latency = latency
            self.ema_variance = 0.0
            self.integral_error = 0.0
            self.was_saturated_max = False
            self.last_setpoint = setpoint
        
        # Initialize EMAs on first call
        if self.ema_latency is None:
            self.ema_latency = latency
        if self.ema_variance is None:
            self.ema_variance = 0.0
        
        error = latency - setpoint
        
        # Update EMA of latency (trend recency: recent observations weighted more)
        self.ema_latency = self.ema_alpha * latency + (1 - self.ema_alpha) * self.ema_latency
        
        # Update EMA of variance (volatility suppression)
        dev = latency - self.ema_latency
        self.ema_variance = self.ema_alpha * (dev * dev) + (1 - self.ema_alpha) * self.ema_variance
        
        # Check saturation conditions
        is_saturated_max = self.current_limit >= self.max_limit - 1e-9
        is_saturated_min = self.current_limit <= self.min_limit + 1e-9
        
        # Anti-windup: don't accumulate error when saturated in the wrong direction
        if not (is_saturated_max and error > 0) and not (is_saturated_min and error < 0):
            self.integral_error += error
        
        # Proportional component with asymmetric gain
        p_component = (self.p_gain_down if error > 0 else self.p_gain_up) * error
        
        # Integral component
        i_component = self.i_gain * self.integral_error
        
        # Anti-windup: when exiting max saturation, cap integral to avoid overshoot
        if self.was_saturated_max and not is_saturated_max and error > 0:
            i_component = min(i_component, abs(p_component) * 2)
        
        # Volatility suppression: be more conservative when variance is high
        variance_damping = 0.05 * self.ema_variance / max(setpoint, 1.0)
        
        # Total correction (normalized by setpoint)
        correction = (p_component + i_component - variance_damping) / max(setpoint, 1.0)
        
        # Apply correction to limit
        new_limit = self.current_limit * (1.0 + correction)
        
        # Clamp to valid range
        self.current_limit = max(self.min_limit, min(self.max_limit, new_limit))
        
        # Update saturation state for next tick
        self.was_saturated_max = is_saturated_max
        
        # Build telemetry with stable schema
        telemetry = {
            'limit': self.current_limit,
            'error': error,
            'ema_latency': self.ema_latency,
            'variance': self.ema_variance,
            'integral': self.integral_error,
            'saturated': 1.0 if (is_saturated_max or is_saturated_min) else 0.0,
            'correction': correction,
        }
        
        return {
            'limit': self.current_limit,
            'telemetry': telemetry,
        }
    
    def snapshot(self) -> str:
        state = {
            'min_limit': self.min_limit,
            'max_limit': self.max_limit,
            'initial_limit': self.initial_limit,
            'current_limit': self.current_limit,
            'ema_latency': self.ema_latency,
            'ema_variance': self.ema_variance,
            'ema_alpha': self.ema_alpha,
            'integral_error': self.integral_error,
            'i_gain': self.i_gain,
            'p_gain_down': self.p_gain_down,
            'p_gain_up': self.p_gain_up,
            'was_saturated_max': self.was_saturated_max,
            'last_setpoint': self.last_setpoint,
        }
        return json.dumps(state)
    
    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        
        c = cls({
            'min_limit': state['min_limit'],
            'max_limit': state['max_limit'],
            'initial_limit': state['initial_limit'],
        })
        
        c.current_limit = state['current_limit']
        c.ema_latency = state['ema_latency']
        c.ema_variance = state['ema_variance']
        c.ema_alpha = state['ema_alpha']
        c.integral_error = state['integral_error']
        c.i_gain = state['i_gain']
        c.p_gain_down = state['p_gain_down']
        c.p_gain_up = state['p_gain_up']
        c.was_saturated_max = state['was_saturated_max']
        c.last_setpoint = state['last_setpoint']
        
        return c
