TEST_CASES = [
    {
        "id": "basic_time_range_week",
        "input": "sessions from last week",
        "expected_filter": "filter_0_column=created_at&filter_0_operator=range&filter_0_value=7d",
        "category": "temporal",
    },
    {
        "id": "basic_time_range_month",
        "input": "sessions from last month",
        "expected_filter": "filter_0_column=created_at&filter_0_operator=range&filter_0_value=30d",
        "category": "temporal",
    },
    {
        "id": "channel_whatsapp",
        "input": "WhatsApp sessions",
        "expected_filter": 'filter_0_column=channel&filter_0_operator=any of&filter_0_value=["whatsapp"]',
        "category": "channel",
    },
    {
        "id": "channel_telegram",
        "input": "telegram sessions",
        "expected_filter": 'filter_0_column=channel&filter_0_operator=any of&filter_0_value=["telegram"]',
        "category": "channel",
    },
    {
        "id": "participant_contains",
        "input": "sessions with participant containing 'test'",
        "expected_filter_contains": ["participant", "contains", "test"],
        "category": "participant",
    },
    {
        "id": "status_completed",
        "input": "completed sessions",
        "expected_filter_contains": ["status", "any of", "completed"],
        "category": "status",
    },
    {
        "id": "multi_channel_and_time",
        "input": "WhatsApp sessions from last week",
        "expected_filter_contains": ["channel", "whatsapp", "created_at", "7d"],
        "category": "compound",
    },
    {
        "id": "multi_participant_and_time",
        "input": "sessions from participant john in the last month",
        "expected_filter_contains": ["participant", "john", "created_at", "30d"],
        "category": "compound",
    },
]
