import json
import math


class Controller:
    def __init__(self, config: dict):
        self.min_limit = float(config.get('min_limit', 1.0))
        self.max_limit = float(config.get('max_limit', 200.0))
        self.desired_limit = float(config.get('initial_limit', 50.0))
        
        # Control parameters
        self.k_p_pos = 0.5   # Proportional gain for negative error (upward, gentle)
        self.k_p_neg = 1.2   # Proportional gain for positive error (downward, aggressive)
        self.k_i = 0.02      # Integral gain
        self.decay = 0.95    # Exponential decay for recency weighting
        
        # State
        self.error_history = []
        self.latency_history = []
        self.last_setpoint = None
        self.integral_error = 0.0
    
    def tick(self, obs: dict) -> dict:
        latency = obs['latency_ms']
        setpoint = obs['setpoint_ms']
        
        # Setpoint-change quiet: reset history on setpoint change
        if self.last_setpoint is None or self.last_setpoint != setpoint:
            self.error_history = []
            self.latency_history = []
            self.integral_error = 0.0
            self.last_setpoint = setpoint
        
        # Calculate error
        error = latency - setpoint
        
        # Update history (keep recent observations only)
        self.error_history.append(error)
        self.latency_history.append(latency)
        
        max_history = 50
        if len(self.error_history) > max_history:
            self.error_history = self.error_history[-max_history:]
            self.latency_history = self.latency_history[-max_history:]
        
        # Trend recency: exponentially weighted average of recent errors
        weighted_error = 0.0
        weight_sum = 0.0
        for i, e in enumerate(reversed(self.error_history)):
            w = self.decay ** i
            weighted_error += w * e
            weight_sum += w
        if weight_sum > 0:
            weighted_error /= weight_sum
        else:
            weighted_error = error
        
        # Volatility suppression: calculate standard deviation
        if len(self.latency_history) >= 2:
            mean = sum(self.latency_history) / len(self.latency_history)
            variance = sum((x - mean) ** 2 for x in self.latency_history) / len(self.latency_history)
            std_dev = math.sqrt(variance)
        else:
            std_dev = 0.0
        
        # Volatility factor (higher variance → more conservative)
        volatility_factor = 1.0 + 0.1 * (std_dev / max(setpoint, 1.0))
        
        # Anti-windup: check if we're saturated
        is_saturated_high = self.desired_limit >= self.max_limit
        is_saturated_low = self.desired_limit <= self.min_limit
        
        # Anti-windup: prevent integral from growing when saturated and pushing harder
        if not (is_saturated_high and error > 0) and not (is_saturated_low and error < 0):
            self.integral_error += error
            # Bound integral to prevent unbounded growth
            self.integral_error = max(-50.0, min(50.0, self.integral_error))
        
        # Asymmetric proportional control
        if weighted_error < 0:
            # Latency too low, increase limit (gentle)
            p_adj = -self.k_p_pos * weighted_error / volatility_factor
        else:
            # Latency too high, decrease limit (aggressive)
            p_adj = -self.k_p_neg * weighted_error * volatility_factor
        
        # Integral control
        i_adj = -self.k_i * self.integral_error
        
        # Total adjustment
        adjustment = p_adj + i_adj
        
        # Apply adjustment to desired limit
        self.desired_limit = self.desired_limit + adjustment
        
        # Clamp to valid range
        limit = max(self.min_limit, min(self.max_limit, self.desired_limit))
        
        # Telemetry: same schema every tick
        telemetry = {
            'limit': float(limit),
            'error': float(error),
            'weighted_error': float(weighted_error),
            'std_dev': float(std_dev),
            'volatility_factor': float(volatility_factor),
            'integral_error': float(self.integral_error),
            'adjustment': float(adjustment),
        }
        
        return {
            'limit': limit,
            'telemetry': telemetry
        }
    
    def snapshot(self) -> str:
        state = {
            'desired_limit': self.desired_limit,
            'min_limit': self.min_limit,
            'max_limit': self.max_limit,
            'k_p_pos': self.k_p_pos,
            'k_p_neg': self.k_p_neg,
            'k_i': self.k_i,
            'decay': self.decay,
            'error_history': list(self.error_history),
            'latency_history': list(self.latency_history),
            'last_setpoint': self.last_setpoint,
            'integral_error': self.integral_error,
        }
        return json.dumps(state)
    
    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        controller = cls({
            'min_limit': state['min_limit'],
            'max_limit': state['max_limit'],
            'initial_limit': state['desired_limit'],
        })
        controller.desired_limit = state['desired_limit']
        controller.k_p_pos = state['k_p_pos']
        controller.k_p_neg = state['k_p_neg']
        controller.k_i = state['k_i']
        controller.decay = state['decay']
        controller.error_history = list(state['error_history'])
        controller.latency_history = list(state['latency_history'])
        controller.last_setpoint = state['last_setpoint']
        controller.integral_error = state['integral_error']
        return controller
