import json


class Controller:
    def __init__(self, config: dict):
        self.min_limit = config.get('min_limit', 1.0)
        self.max_limit = config.get('max_limit', 200.0)
        self.limit = config.get('initial_limit', 50.0)
        
        # History of observations
        self.observations = []  # List of (t, error, latency)
        self.last_setpoint = None
        self.was_saturated_last = False
    
    def tick(self, obs: dict) -> dict:
        t = obs['t']
        latency = obs['latency_ms']
        setpoint = obs['setpoint_ms']
        
        # Handle setpoint changes - reset history
        if self.last_setpoint is not None and self.last_setpoint != setpoint:
            self.observations = []
            self.was_saturated_last = False
        
        self.last_setpoint = setpoint
        error = latency - setpoint
        
        # Add observation
        self.observations.append((t, error, latency))
        # Keep last 100 observations
        self.observations = self.observations[-100:]
        
        # Compute weighted error (recent observations weighted more)
        weighted_error = 0.0
        weight_sum = 0.0
        decay_tau = 20.0
        
        for hist_t, hist_error, _ in self.observations:
            age = t - hist_t
            weight = 2.0 ** (-age / decay_tau)
            weighted_error += hist_error * weight
            weight_sum += weight
        
        weighted_error = weighted_error / weight_sum if weight_sum > 0 else 0.0
        
        # Compute variance of recent latencies
        if len(self.observations) >= 2:
            recent_lats = [lat for _, _, lat in self.observations[-20:]]
            mean_lat = sum(recent_lats) / len(recent_lats)
            variance = sum((x - mean_lat) ** 2 for x in recent_lats) / len(recent_lats)
        else:
            variance = 0.0
        
        # Check saturation
        is_saturated = abs(self.limit - self.max_limit) < 1e-6
        
        # Anti-windup: after saturation ends, use immediate error only
        if self.was_saturated_last and error <= 0:
            control_input = error
            antiwinding_active = 1.0
        else:
            control_input = weighted_error
            antiwinding_active = 0.0
        
        # Asymmetric response: downward correction > 2x upward correction
        # error > 0: latency too high, reduce limit strongly (gain = -0.15)
        # error <= 0: latency too low, increase limit weakly (gain = 0.05)
        # Magnitude ratio: 0.15 / 0.05 = 3.0 > 2.0
        if error > 0:
            gain = -0.15
        else:
            gain = 0.05
        
        # Volatility suppression: higher variance -> more conservative limit
        volatility_factor = 1.0 - 0.1 * min(variance / 30.0, 1.0)
        
        # Compute correction
        correction = control_input * gain * volatility_factor
        
        # Update limit (clamped to valid range)
        new_limit = self.limit + correction
        new_limit = max(self.min_limit, min(self.max_limit, new_limit))
        self.limit = new_limit
        
        # Update saturation state for next tick
        self.was_saturated_last = is_saturated
        
        # Fixed telemetry schema (same keys every tick)
        telemetry = {
            'limit': self.limit,
            'error': error,
            'weighted_error': weighted_error,
            'variance': variance,
            'gain': gain,
            'correction': correction,
            'volatility_factor': volatility_factor,
            'saturated': 1.0 if is_saturated else 0.0,
            'antiwinding_active': antiwinding_active,
            'control_input': control_input,
        }
        
        return {
            'limit': self.limit,
            'telemetry': telemetry,
        }
    
    def snapshot(self) -> str:
        state = {
            'limit': self.limit,
            'min_limit': self.min_limit,
            'max_limit': self.max_limit,
            'last_setpoint': self.last_setpoint,
            'observations': self.observations,
            'was_saturated_last': self.was_saturated_last,
        }
        return json.dumps(state)
    
    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        controller = cls.__new__(cls)
        controller.limit = state['limit']
        controller.min_limit = state['min_limit']
        controller.max_limit = state['max_limit']
        controller.last_setpoint = state['last_setpoint']
        controller.observations = state['observations']
        controller.was_saturated_last = state['was_saturated_last']
        return controller
