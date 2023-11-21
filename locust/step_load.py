import math

from locust import LoadTestShape


class StepLoadShape(LoadTestShape):
    """
    A step load shape


    Keyword arguments:

        step_time -- Time between steps
        step_load -- User increase amount at each step
        spawn_rate -- Users to stop/start per second at every step
        time_limit -- Time limit in seconds

    """

    use_common_options = True

    step_time = 60
    step_load = 5
    spawn_rate = 1
    time_limit = 300

    def tick(self):
        time_limit = self.runner.environment.parsed_options.run_time or self.time_limit
        run_time = self.get_run_time()

        if run_time > time_limit:
            return None

        current_step = math.floor(run_time / self.step_time) + 1
        return current_step * self.step_load, self.spawn_rate
