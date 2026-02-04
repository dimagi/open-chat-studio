import json

import openai

SCHEMA = """
## Available Fields for sessions

### Created At (`created_at`)
- Type: timestamp
- Operators: on, before, after, range
- Range values: 1h, 1d, 7d, 30d, 90d

### Channel (`channel`)
- Type: choice
- Operators: any of, excludes
- Options: web, whatsapp, telegram, sms

### Participant (`participant`)
- Type: string
- Operators: equals, contains, starts with

### Status (`status`)
- Type: choice
- Operators: any of, excludes
- Options: pending, active, completed
"""

SYSTEM_PROMPT = """Convert natural language to filter query strings.

Output format: filter_0_column=X&filter_0_operator=Y&filter_0_value=Z
For multiple conditions, increment index: filter_1_column=...

Use temporal shortcuts: 7d (week), 30d (month), 1d (day)
For choice fields, value is JSON array: ["whatsapp"]

Return ONLY valid JSON: {"filter_query_string": "...", "explanation": "...", "confidence": 0.9}
"""


class NLFilterAgent:
    def __init__(self):
        self.client = openai.OpenAI()

    def translate(self, nl_query: str, table_type: str = "sessions") -> dict:
        response = self.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT + "\n" + SCHEMA},
                {"role": "user", "content": f'Query: "{nl_query}"\n\nReturn JSON:'},
            ],
            temperature=0.1,
        )
        return json.loads(response.choices[0].message.content)
