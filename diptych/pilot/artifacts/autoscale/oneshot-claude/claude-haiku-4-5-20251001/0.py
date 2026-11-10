import math

class Controller:
    def __init__(self, config: dict):
        self.min_replicas = config.get('min_replicas', 1)
        self.max_replicas = config.get('max_replicas', 60)
        self.initial_replicas = config.get('initial_replicas', 10)
        self.target_utilization = config.get('target_utilization', 0.6)
        self.tolerance = config.get('tolerance', 0.1)
        self.stabilization_ticks = config.get('stabilization_ticks', 20)
        
        self.current_replicas = self.initial_replicas
        self.base_recommendations = []
        
    def tick(self, obs: dict) -> dict:
        base_rec = self._compute_recommendation(obs)
        self.base_recommendations.append(base_rec)
        
        # Keep bounded memory: only need last stabilization_ticks + 10
        if len(self.base_recommendations) > self.stabilization_ticks + 10:
            self.base_recommendations.pop(0)
        
        # Downscale stabilization: never go below max of recent recommendations
        window_size = min(self.stabilization_ticks, len(self.base_recommendations))
        window = self.base_recommendations[-window_size:]
        min_allowed_by_stability = max(window)
        
        rec = max(base_rec, min_allowed_by_stability)
        
        # Scale-up rate limit: can add at most max(4, current) per tick
        max_increase = max(4, self.current_replicas)
        rec = min(rec, self.current_replicas + max_increase)
        
        # Bound by configured limits
        new_replicas = max(self.min_replicas, min(self.max_replicas, rec))
        
        self.current_replicas = new_replicas
        
        return {
            'replicas': self.current_replicas,
            'telemetry': {
                'base_recommendation': base_rec,
            }
        }
    
    def _compute_recommendation(self, obs: dict) -> int:
        pods = obs.get('pods', [])
        
        ready_with_metrics = []
        ready_without_metrics = 0
        starting_count = 0
        
        for pod in pods:
            if pod.get('ready', False):
                if pod.get('utilization') is not None:
                    ready_with_metrics.append(pod['utilization'])
                else:
                    ready_without_metrics += 1
            else:
                starting_count += 1
        
        k = len(ready_with_metrics)
        
        if k == 0:
            return self.current_replicas
        
        avg_u = sum(ready_with_metrics) / k
        ratio = avg_u / self.target_utilization
        
        if abs(ratio - 1.0) <= self.tolerance:
            return self.current_replicas
        
        rec = math.ceil(k * avg_u / self.target_utilization)
        
        # Missing metrics damp scaling: stay between current and full decision
        total_ready = k + ready_without_metrics
        if ready_without_metrics > 0:
            avg_with_missing = (sum(ready_with_metrics) + ready_without_metrics * avg_u) / total_ready
            rec_with_missing = math.ceil(total_ready * avg_with_missing / self.target_utilization)
            if rec > self.current_replicas:
                rec = min(rec, rec_with_missing)
        
        # Starting pods don't enlarge scale-up
        if starting_count > 0:
            total = total_ready + starting_count
            sum_u = sum(ready_with_metrics) + ready_without_metrics * avg_u
            avg_with_starting = sum_u / total
            rec_with_starting = math.ceil(total * avg_with_starting / self.target_utilization)
            if rec > self.current_replicas:
                rec = min(rec, rec_with_starting)
                rec = max(rec, self.current_replicas)
        
        return rec
