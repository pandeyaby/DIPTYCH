import math

class Controller:
    def __init__(self, config: dict):
        self.config = config
        self.min_replicas = config.get('min_replicas', 1)
        self.max_replicas = config.get('max_replicas', 60)
        self.initial_replicas = config.get('initial_replicas', 10)
        self.target_utilization = config.get('target_utilization', 0.6)
        self.tolerance = config.get('tolerance', 0.1)
        self.stabilization_ticks = config.get('stabilization_ticks', 20)
        
        self.base_recommendations = []
    
    def tick(self, obs: dict) -> dict:
        t = obs['t']
        current_replicas = obs['replicas']
        pods = obs['pods']
        
        # Categorize pods
        ready_pods = [p for p in pods if p['ready']]
        starting_pods = [p for p in pods if not p['ready']]
        ready_with_metrics = [p for p in ready_pods if p['utilization'] is not None]
        
        # Calculate base recommendation (Kubernetes formula)
        if ready_with_metrics:
            avg_utilization = sum(p['utilization'] for p in ready_with_metrics) / len(ready_with_metrics)
            utilization_ratio = avg_utilization / self.target_utilization
            
            if abs(utilization_ratio - 1.0) <= self.tolerance:
                base_recommendation = current_replicas
            else:
                base_recommendation = math.ceil(
                    len(ready_with_metrics) * avg_utilization / self.target_utilization
                )
        else:
            base_recommendation = current_replicas
            avg_utilization = None
        
        # Track base recommendation for stabilization window
        self.base_recommendations.append(base_recommendation)
        if len(self.base_recommendations) > self.stabilization_ticks + 10:
            self.base_recommendations = self.base_recommendations[-(self.stabilization_ticks + 10):]
        
        desired_replicas = base_recommendation
        
        # Requirement 4: Missing metrics damp the decision
        if len(ready_pods) > len(ready_with_metrics):
            if desired_replicas > current_replicas:
                desired_replicas = (desired_replicas + current_replicas) // 2
            elif desired_replicas < current_replicas:
                desired_replicas = (desired_replicas + current_replicas + 1) // 2
        
        # Requirement 5: Starting pods cap scale-up
        if starting_pods:
            total_pods = len(ready_with_metrics) + len(starting_pods)
            
            if ready_with_metrics:
                avg_with_starting = sum(p['utilization'] for p in ready_with_metrics) / total_pods
            else:
                avg_with_starting = 0
            
            ratio_with_starting = avg_with_starting / self.target_utilization
            
            if abs(ratio_with_starting - 1.0) <= self.tolerance:
                scale_up_cap = current_replicas
            else:
                scale_up_cap = math.ceil(total_pods * avg_with_starting / self.target_utilization)
            
            scale_up_cap = max(scale_up_cap, current_replicas)
            desired_replicas = min(desired_replicas, scale_up_cap)
        
        # Requirement 2: Scale-up rate limit
        if desired_replicas > current_replicas:
            max_increase = max(4, current_replicas)
            desired_replicas = min(desired_replicas, current_replicas + max_increase)
        
        # Requirement 3: Downscale stabilization
        if desired_replicas < current_replicas and self.base_recommendations:
            stabilization_window = self.base_recommendations[-self.stabilization_ticks:]
            max_recommendation_in_window = max(stabilization_window)
            desired_replicas = max(desired_replicas, max_recommendation_in_window)
        
        # Bound to [min_replicas, max_replicas]
        desired_replicas = max(self.min_replicas, min(desired_replicas, self.max_replicas))
        
        # Prepare telemetry
        telemetry = {
            'current_replicas': current_replicas,
            'desired_replicas': desired_replicas
        }
        if avg_utilization is not None:
            telemetry['average_utilization'] = avg_utilization
        
        return {
            'replicas': desired_replicas,
            'telemetry': telemetry
        }
