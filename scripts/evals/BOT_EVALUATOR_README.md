# Bot Evaluator for Open Chat Studio

This script evaluates chatbot responses by sending inputs from a CSV dataset to an OCS bot via API and evaluating the responses using LangChain.

## Features

- **CSV Dataset Support**: Read test inputs from CSV files
- **OCS API Integration**: Communicate with Open Chat Studio bots via the official API
- **LangChain Evaluation**: Use LLMs to evaluate bot responses with customizable prompts
- **Comprehensive Results**: Generate detailed evaluation reports with scores, reasoning, and metrics
- **Parallel Processing**: Efficient async/await implementation with configurable concurrency for faster evaluation

## Setup

1. Set up your OpenAI API key for the evaluator LLM:
    
   ```bash
    export OPENAI_API_KEY="your-openai-api-key"
    ```

2. Set up your OCS API key:

    ```bash
    export OCS_API_KEY="your-ocs-api-key"
    ```
   
3. Install UV

   See https://docs.astral.sh/uv/getting-started/installation/

4. Copy the evaluator file

   You can get the script by cloning the OCS repo and checking out the `sk/evals-poc` branch or you can copy the script from GitHub: [bot_evaluator.py](https://github.com/dimagi/open-chat-studio/blob/sk/evals-poc/scripts/evals/bot_evaluator.py)

## Basic usage

```shell
uv run bot_evaluator.py \
  --csv example_dataset.csv \
  --experiment-id "your-experiment-uuid"
```

## Advanced Usage

```bash
uv run bot_evaluator.py \
  --csv ~/Downloads/coach_dataset1.csv \
  --experiment-id "e2b4855f-8550-47ff-87d2-d92018676ff3" \
  --api-key $OCS_API_KEY \
  --eval-mode "binary" \
  --max-concurrency 5 \
  --verbose
```

### Command Line Options

- `--csv`: Path to CSV file with test data (required)
- `--experiment-id`: UUID of the OCS experiment/chatbot (required)
- `--api-key`: Your OCS API key (required)
- `--base-url`: OCS instance URL (default: https://chatbots.dimagi.com)
- `--input-column`: CSV column name for input text (default: "Input from the partcipant")
- `--scenario-column`: CSV column name for the 'scenario' (default: "Scenario text")
- `--expected-category-column`: CSV column name for the expected response category (default: "Response category")
- `--expected-response-column`: CSV column name for expected response (default: "Expected Response")
- `--participant-data-column`: CSV column name for participant data (optional)
- `--session-data-column`: CSV column name for session data (optional)
- `--history-column`: CSV column name for conversation history. Data must be a string, JSON object with 'role' and 'content' keys, or list of JSON objects with 'role' and 'content' keys (optional)
- `--output`: Output CSV file path (default: "evaluation_results.csv")
- `--evaluator-model`: LLM model for evaluation (default: "gpt-4o-mini")
- `--custom-prompt`: Custom evaluation prompt (optional)
- `--custom-eval-message`: Custom evaluation message template (optional)
- `--eval-mode`: Evaluation mode: "score" (1-10) or "binary" (true/false) (default: "score")
- `--max-concurrency`: Maximum number of concurrent evaluations (default: 10)
- `--verbose`: Enable detailed logging (optional)
- `--validate`: Validate the input file. This won't do any evals, it will only output error messages if there are any.
- `--limit`: Limit the number of rows that are processed to this number.

## CSV Format

Your CSV file should have at least one column with input text. Additional columns can provide expected outputs, participant data, session data, and conversation history. Example:

```csv
input,expected_output,participant_data,session_data,history
"What is your name?","I am a helpful AI assistant.","{""age"": 25}","{""context"": ""first_visit""}","User just started the conversation"
"How can you help me?","I can help you with various tasks...","{""role"": ""student""}","{""topic"": ""support""}","Previous: What is your name?"
"What is the capital of France?","The capital of France is Paris.","{""level"": ""beginner""}","","User asking geography questions"
```

### Optional CSV Columns

- **participant_data**: JSON string containing participant information (age, role, preferences, etc.)
- **session_data**: JSON string containing session context (current topic, state, etc.)
- **history**: Text describing conversation history or scenario context
- **expected_output**: Expected bot response for comparison

## Evaluation Modes

The script supports two evaluation modes:

### Score Mode (Default)
The default evaluation prompt assesses responses based on:

1. **Relevance**: How well does the response address the user's question?
2. **Accuracy**: Is the information provided correct and factual?
3. **Helpfulness**: Does the response provide useful information?
4. **Clarity**: Is the response clear and easy to understand?
5. **Completeness**: Does the response fully address the user's needs?

Scores range from 1-10:
- 1-3: Poor response (unhelpful, incorrect, or irrelevant)
- 4-6: Average response (partially helpful but has significant issues)
- 7-8: Good response (helpful and mostly accurate)
- 9-10: Excellent response (comprehensive, accurate, and very helpful)

### Binary Mode
When using `--eval-mode binary`, the evaluator returns true/false judgments instead of numeric scores. This is useful for pass/fail evaluations or when you need simple binary classifications.

## Output Format

The evaluation results are saved to a CSV file with the following columns:

- `input_text`: Original input from the dataset
- `bot_response`: Response from the OCS bot
- `expected_response`: Expected bot response
- `evaluation_result`: Score from 1-10 (score mode) or true/false (binary mode)
- `evaluation_reasoning`: Detailed reasoning for the score
- `response_time`: Time taken for the bot to respond (seconds)
- `session_id`: OCS session ID for this interaction
- `timestamp`: When the evaluation was performed

## Custom Evaluation Prompts

You can provide custom evaluation prompts and messages to focus on specific aspects:

```bash
# Custom evaluation prompt
python bot_evaluator.py \
  --csv dataset.csv \
  --experiment-id "your-id" \
  --api-key "your-key" \
  --custom-prompt "Rate this medical advice response for accuracy and safety. Consider if the response appropriately refers to healthcare professionals when needed. Score 1-10."

# Custom evaluation message template
python bot_evaluator.py \
  --csv dataset.csv \
  --experiment-id "your-id" \
  --api-key "your-key" \
  --custom-eval-message "User Input: {input_text}\nBot Response: {bot_response}\nExpected Response: {expected_output}\n\nEvaluate the bot's response quality."
```

### Template Variables
When using `--custom-eval-message`, you can use these variables:
- `{input_text}`: Original user input
- `{bot_response}`: Bot's response
- `{expected_output}`: Expected response (if provided)
- `{history}`: Conversation history (if provided)

## Error Handling

The script handles various error conditions:

- API connection issues
- Invalid experiment IDs
- Missing CSV columns
- LLM evaluation failures
- Network timeouts

Failed evaluations are recorded with a score of 0 and error details in the reasoning field.

## API Authentication

The script supports both API key and token authentication as defined in the OCS API schema:
- API Key: Pass via `--api-key` argument (sent as `X-api-key` header)
- Token: Set `BEARER_TOKEN` environment variable

## Performance Considerations

- The script processes evaluations in parallel with configurable concurrency (default: 10 concurrent evaluations)
- Use `--max-concurrency` to adjust the number of parallel evaluations based on your API limits
- Default timeout for bot responses is 30 seconds
- Use `gpt-4o-mini` for faster and more cost-effective evaluations
- Consider adjusting concurrency for large datasets to balance speed and API rate limits

## Troubleshooting

1. **"Failed to start session"**: Check your experiment ID and API key
2. **"Input column not found"**: Verify your CSV column names match the `--input-column` parameter
3. **"Evaluation failed"**: Check your OpenAI API key and quota
4. **Timeout errors**: Increase timeout or check bot performance
5. **High error rate**: Try reducing `--max-concurrency` to avoid overwhelming the API
6. **JSON parsing errors**: Ensure participant_data and session_data columns contain valid JSON
7. **Rate limit errors**: Use `--verbose` to see detailed error messages and adjust concurrency

## Development

The script is structured with these main components:

- `OCSAPIClient`: Handles OCS API interactions
- `BotEvaluator`: Manages LangChain evaluation logic
- `EvaluationResult`: Data class for results
- `EvaluationOutput`: Pydantic model for LLM output parsing
