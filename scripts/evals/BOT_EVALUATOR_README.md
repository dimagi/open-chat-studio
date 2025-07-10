# Bot Evaluator for Open Chat Studio

This script evaluates chatbot responses by sending inputs from a CSV dataset to an OCS bot via API and evaluating the responses using LangChain.

## Features

- **CSV Dataset Support**: Read test inputs from CSV files
- **OCS API Integration**: Communicate with Open Chat Studio bots via the official API
- **LangChain Evaluation**: Use LLMs to evaluate bot responses with customizable prompts
- **Comprehensive Results**: Generate detailed evaluation reports with scores, reasoning, and metrics
- **Async Processing**: Efficient async/await implementation for faster evaluation

## Installation

1. Install required dependencies:
```bash
pip install -r requirements_evaluator.txt
```

2. Set up your OpenAI API key for the evaluator LLM:
```bash
export OPENAI_API_KEY="your-openai-api-key"
```

## Usage

### Basic Usage

```bash
python bot_evaluator.py \
  --csv example_dataset.csv \
  --experiment-id "your-experiment-uuid" \
  --api-key "your-ocs-api-key"
```

### Advanced Usage

```bash
python bot_evaluator.py \
  --csv my_test_data.csv \
  --experiment-id "123e4567-e89b-12d3-a456-426614174000" \
  --api-key "your-ocs-api-key" \
  --base-url "https://your-ocs-instance.com" \
  --input-column "question" \
  --expected-output-column "ideal_answer" \
  --output "my_evaluation_results.csv" \
  --evaluator-model "gpt-4o" \
  --custom-prompt "Evaluate this customer service response for helpfulness and accuracy. Score 1-10."
```

### Command Line Options

- `--csv`: Path to CSV file with test data (required)
- `--experiment-id`: UUID of the OCS experiment/chatbot (required)
- `--api-key`: Your OCS API key (required)
- `--base-url`: OCS instance URL (default: https://chatbots.dimagi.com)
- `--input-column`: CSV column name for input text (default: "input")
- `--expected-output-column`: CSV column name for expected output (optional)
- `--output`: Output CSV file path (default: "evaluation_results.csv")
- `--evaluator-model`: LLM model for evaluation (default: "gpt-4o-mini")
- `--custom-prompt`: Custom evaluation prompt (optional)

## CSV Format

Your CSV file should have at least one column with input text. Example:

```csv
input,expected_output
"What is your name?","I am a helpful AI assistant."
"How can you help me?","I can help you with various tasks..."
"What is the capital of France?","The capital of France is Paris."
```

## Evaluation Criteria

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

## Output Format

The evaluation results are saved to a CSV file with the following columns:

- `input_text`: Original input from the dataset
- `bot_response`: Response from the OCS bot
- `evaluation_score`: Score from 1-10
- `evaluation_reasoning`: Detailed reasoning for the score
- `response_time`: Time taken for the bot to respond (seconds)
- `session_id`: OCS session ID for this interaction
- `timestamp`: When the evaluation was performed

## Custom Evaluation Prompts

You can provide custom evaluation prompts to focus on specific aspects:

```bash
python bot_evaluator.py \
  --csv dataset.csv \
  --experiment-id "your-id" \
  --api-key "your-key" \
  --custom-prompt "Rate this medical advice response for accuracy and safety. Consider if the response appropriately refers to healthcare professionals when needed. Score 1-10."
```

## Example Results

After running the evaluation, you'll see output like:

```
INFO:__main__:Processing row 1/10: What is your name?...
INFO:__main__:Row 1 completed - Score: 8.5
INFO:__main__:Processing row 2/10: How can you help me?...
INFO:__main__:Row 2 completed - Score: 7.2
...
INFO:__main__:Results saved to evaluation_results.csv
INFO:__main__:Evaluation Summary:
INFO:__main__:  Average Score: 7.85/10
INFO:__main__:  Average Response Time: 2.34s
INFO:__main__:  Total Evaluations: 10
```

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

- The script processes evaluations sequentially to avoid overwhelming the OCS API
- Default timeout for bot responses is 30 seconds
- Use `gpt-4o-mini` for faster and more cost-effective evaluations
- Consider rate limiting for large datasets

## Troubleshooting

1. **"Failed to start session"**: Check your experiment ID and API key
2. **"Input column not found"**: Verify your CSV column names
3. **"Evaluation failed"**: Check your OpenAI API key and quota
4. **Timeout errors**: Increase timeout or check bot performance

## Development

The script is structured with these main components:

- `OCSAPIClient`: Handles OCS API interactions
- `BotEvaluator`: Manages LangChain evaluation logic
- `EvaluationResult`: Data class for results
- `EvaluationOutput`: Pydantic model for LLM output parsing

## License

This script is part of the Open Chat Studio project and follows the same license terms.