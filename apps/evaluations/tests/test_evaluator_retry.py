# apps/evaluations/tests/test_evaluator_retry.py


class TestLlmEvaluatorRetry:
    def test_retry_includes_rate_limit_exceptions(self):
        """Verify that the evaluator retry includes rate limit exceptions."""
        import anthropic

        # RATE_LIMIT_EXCEPTIONS should be imported from the retry module
        import openai
        from google.api_core.exceptions import ResourceExhausted

        from apps.evaluations.evaluators import RATE_LIMIT_EXCEPTIONS

        assert openai.RateLimitError in RATE_LIMIT_EXCEPTIONS
        assert anthropic.RateLimitError in RATE_LIMIT_EXCEPTIONS
        assert ResourceExhausted in RATE_LIMIT_EXCEPTIONS
