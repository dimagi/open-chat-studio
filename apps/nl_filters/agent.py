import json
import os


class NLFilterAgent:
    """Agent to translate natural language queries to filter query strings."""

    # Hardcoded schema for the "sessions" table
    SESSIONS_SCHEMA = {
        "created_at": {"type": "timestamp", "operators": ["on", "before", "after", "range"]},
        "channel": {"type": "choice", "operators": ["any of", "excludes"]},
        "participant": {"type": "string", "operators": ["equals", "contains"]},
        "status": {"type": "choice", "operators": ["any of", "excludes"]},
    }

    SYSTEM_PROMPT = """You are a filter query translator. Convert natural language queries into filter query strings.

Available fields and operators:
- created_at (timestamp): on, before, after, range
  - For range, use temporal shortcuts: 1h, 1d, 7d, 15d, 30d, 90d, 365d
- channel (choice): any of, excludes
- participant (string): equals, contains
- status (choice): any of, excludes

Output format:
filter_0_column=COLUMN&filter_0_operator=OPERATOR&filter_0_value=VALUE

For multiple filters:
filter_0_column=X&filter_0_operator=Y&filter_0_value=Z&filter_1_column=A&filter_1_operator=B&filter_1_value=C

Examples:
- "sessions from last week" → filter_0_column=created_at&filter_0_operator=range&filter_0_value=7d
- "WhatsApp sessions" → filter_0_column=channel&filter_0_operator=any of&filter_0_value=["whatsapp"]
- "active sessions from last month" → filter_0_column=created_at&filter_0_operator=range&filter_0_value
=30d&filter_1_column=status&filter_1_operator=any of&filter_1_value=["active"]

Return a JSON object with:
- filter_query_string: the filter query string
- explanation: brief explanation of the translation
- confidence: float between 0 and 1
"""

    def __init__(self):
        self.langfuse_client = None
        self._initialize_langfuse()

    def _initialize_langfuse(self):
        public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
        secret_key = os.getenv("LANGFUSE_SECRET_KEY")
        host = os.getenv("LANGFUSE_HOST")

        if not all([public_key, secret_key, host]):
            print("Warning! Langfuse credentials not configured. Tracing will be disabled.")
            return

        from langfuse import Langfuse

        self.langfuse_client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
        )
        print(f"Langfuse client initialized: {host}")

    def translate(self, nl_query: str, table_type: str = "sessions") -> dict:
        """
        Translate a natural language query to a filter query string.

        Args:
            nl_query: Natural language query
            table_type: Table type (currently only "sessions" supported)

        Returns:
            dict with keys: filter_query_string, explanation, confidence, trace_id
        """
        if table_type != "sessions":
            raise ValueError(f"Unsupported table type: {table_type}")

        # Create trace if Langfuse is configured
        trace = None
        if self.langfuse_client:
            trace = self.langfuse_client.trace(
                name="nl_filter_translation",
                input={"nl_query": nl_query, "table_type": table_type},
            )

        try:
            result = self._call_llm(nl_query, trace)

            if trace:
                trace.update(
                    output={
                        "filter_query_string": result["filter_query_string"],
                        "explanation": result["explanation"],
                        "confidence": result["confidence"],
                    }
                )
            result["trace_id"] = trace.id if trace else None

            return result

        except Exception as e:
            if trace:
                trace.update(
                    output={"error": str(e)},
                    status_message=str(e),
                )
            raise

    def _call_llm(self, nl_query: str, trace) -> dict:
        """Call the LLM to translate the query."""
        # Use OpenAI directly for POC
        import openai

        openai_api_key = os.getenv("OPENAI_API_KEY")
        if not openai_api_key:
            raise ValueError("OPENAI_API_KEY environment variable not set")

        client = openai.OpenAI(api_key=openai_api_key)

        # Create generation span in Langfuse
        generation = None
        if trace:
            generation = trace.generation(
                name="llm_call",
                model="gpt-4o-mini",
                input=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": nl_query},
                ],
            )

        # Call OpenAI
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": nl_query},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content
        result = json.loads(content)

        # Update generation with output and usage
        if generation:
            generation.update(
                output=result,
                usage={
                    "input": response.usage.prompt_tokens,
                    "output": response.usage.completion_tokens,
                    "total": response.usage.total_tokens,
                },
            )

        return result

    def record_feedback(self, trace_id: str, is_correct: bool):
        """
        Record user feedback for a trace.

        Args:
            trace_id: The Langfuse trace ID
            is_correct: Whether the translation was correct
        """
        if not self.langfuse_client:
            print("Warning: Langfuse not configured. Cannot record feedback.")
            return

        score_value = 1.0 if is_correct else 0.0
        score_name = "user_feedback"

        self.langfuse_client.score(
            trace_id=trace_id,
            name=score_name,
            value=score_value,
            comment="User feedback: " + ("correct" if is_correct else "incorrect"),
        )
        print(f"✓ Feedback recorded: {score_name}={score_value}")

    def flush(self):
        """Flush any pending data to Langfuse."""
        if self.langfuse_client:
            self.langfuse_client.flush()
