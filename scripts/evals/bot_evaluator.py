#!/usr/bin/env python3
"""
Bot Evaluation Script for Open Chat Studio

This script evaluates a bot by:
1. Reading test data from a CSV file
2. Sending inputs to the bot via OCS API
3. Evaluating responses using LangChain/LangGraph
4. Generating evaluation results

Usage:
    python bot_evaluator.py --csv dataset.csv --experiment-id <uuid> --api-key <key>
"""

import argparse
import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, ClassVar, Literal, Self

import aiohttp
import pandas as pd
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
import contextlib

# Configure logging
logging.basicConfig(level=logging.WARN)
logger = logging.getLogger("eval")


@dataclass
class EvaluationResult:
    """Data class for evaluation results"""

    input_row_index: int
    input_text: str
    bot_response: str
    expected_category: str
    expected_response: str
    evaluation_result: Any
    evaluation_reasoning: str
    response_time: float
    session_id: str
    timestamp: str
    model: str


class EvaluationScoreOutput(BaseModel):
    """Pydantic model for evaluation output for score output"""

    DEFAULT_PROMPT: ClassVar[str] = """You are an expert evaluator of chatbot responses. 
            You will evaluate how well a chatbot responded to a user's input based on the following criteria:
            
            1. Relevance: How well does the response address the user's question or request?
            2. Accuracy: Is the information provided correct and factual?
            3. Helpfulness: Does the response provide useful information or assistance?
            4. Clarity: Is the response clear and easy to understand?
            5. Completeness: Does the response fully address the user's needs?
            
            Provide a score from 1-10 where:
            - 1-3: Poor response (unhelpful, incorrect, or irrelevant)
            - 4-6: Average response (partially helpful but has significant issues)
            - 7-8: Good response (helpful and mostly accurate)
            - 9-10: Excellent response (comprehensive, accurate, and very helpful)
            """

    result: float = Field(description="Score from 1-10 where 10 is best")
    reasoning: str = Field(description="Detailed reasoning for the score")

    @classmethod
    def error(cls, message: str) -> Self:
        return cls(result=0.0, reasoning=message)


class EvaluationBinaryOutput(BaseModel):
    """Pydantic model for evaluation output for 'true' / 'false' output"""

    DEFAULT_PROMPT: ClassVar[str] = """You are an expert evaluator of chatbot responses. 
            You will evaluate how well a chatbot responded to a user's input based on the following criteria:
            
            1. Relevance: How well does the response address the user's question or request?
            2. Accuracy: Is the information provided correct and factual?
            3. Helpfulness: Does the response provide useful information or assistance?
            4. Clarity: Is the response clear and easy to understand?
            5. Completeness: Does the response fully address the user's needs?
            
            Respond with `true` if the response meets all these critia otherwise respond with `false`
            """

    result: bool = Field(description="Evaluation result")
    reasoning: str = Field(description="Detailed reasoning for the result")

    @classmethod
    def error(cls, message: str) -> Self:
        return cls(result=False, reasoning=message)


class OCSAPIClient:
    """Client for interacting with Open Chat Studio API"""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.session = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(headers={"X-api-key": self.api_key})
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def start_chat_session(
        self,
        experiment_id: str,
        participant_id: str,
        session_data: dict[str, Any] = None,
        history_data: list[dict[str, Any]] = None,
    ) -> str:
        """Start a new chat session"""
        url = f"{self.base_url}/api/sessions/"
        payload = {
            "experiment": experiment_id,
            "state": session_data or {},
            "participant": participant_id,
            "messages": history_data or [],
        }

        async with self.session.post(url, json=payload) as response:
            if response.status == 201:
                data = await response.json()
                return data["id"]
            else:
                error_text = await response.text()
                raise Exception(f"Failed to start session: {response.status} - {error_text}")

    async def send_message(self, session_id: str, message: str) -> str:
        """Send a message to the chat session"""
        url = f"{self.base_url}/api/chat/{session_id}/message/"
        payload = {"message": message}

        async with self.session.post(url, json=payload) as response:
            if response.status == 202:
                data = await response.json()
                return data["task_id"]
            else:
                error_text = await response.text()
                raise Exception(f"Failed to send message: {response.status} - {error_text}")

    async def poll_for_response(self, session_id: str, task_id: str, timeout: int = 30) -> dict[str, Any]:
        """Poll for new messages in the chat session"""
        url = f"{self.base_url}/api/chat/{session_id}/{task_id}/poll/"

        start_time = time.time()
        while time.time() - start_time < timeout:
            async with self.session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    message = data.get("message", None)
                    if message:
                        return message

                    await asyncio.sleep(1)
                else:
                    error_text = await response.text()
                    logger.warning(f"Poll failed: {response.status} - {error_text}")
                    await asyncio.sleep(1)

        logger.warning("Poll timed out")
        return {}

    async def get_bot_response(
        self,
        experiment_id: str,
        input_text: str,
        session_data: dict[str, Any] = None,
        participant_data: dict[str, Any] = None,
        history_data: list[dict[str, Any]] = None,
    ) -> tuple[str, str, float]:
        """Get bot response for input text"""
        start_time = time.time()

        participant_id = await self.create_participant(experiment_id, participant_data)

        # Start session
        session_id = await self.start_chat_session(experiment_id, participant_id, session_data, history_data)

        # Send message
        task_id = await self.send_message(session_id, input_text)

        # Poll for response
        message = await self.poll_for_response(session_id, task_id)

        response_time = time.time() - start_time

        # Extract bot response
        bot_response = message.get("content", "")
        return bot_response, session_id, response_time

    async def create_participant(self, experiment_id, participant_data):
        url = f"{self.base_url}/api/participants"
        participant_id = f"eval:{str(uuid.uuid4())}"
        payload = {
            "identifier": participant_id,
            "platform": "api",
            "data": [{"experiment": experiment_id, "data": participant_data}],
        }

        async with self.session.post(url, json=payload) as response:
            if response.status == 200:
                return participant_id
            else:
                error_text = await response.text()
                raise Exception(f"Failed to send message: {response.status} - {error_text}")


class BotEvaluator:
    """Main bot evaluation class"""

    def __init__(
        self,
        evaluator_model: str = "gpt-4o-mini",
        evaluation_mode: Literal["score", "binary"] = "score",
        custom_prompt: str = None,
        max_concurrency: int = 10,
    ):
        self.evaluator_model = evaluator_model
        self.mode = evaluation_mode
        self.max_concurrency = max_concurrency
        self.llm = ChatOpenAI(model=evaluator_model, temperature=0)

        # Load evaluation prompt from file or use custom prompt
        self.output_schema = EvaluationScoreOutput if self.mode == "score" else EvaluationBinaryOutput
        
        if custom_prompt:
            prompt_text = custom_prompt
        else:
            try:
                with open("prompt.txt", "r") as f:
                    prompt_text = f.read()
            except FileNotFoundError:
                prompt_text = self.output_schema.DEFAULT_PROMPT
        
        self.evaluation_prompt = ChatPromptTemplate.from_messages(
            [
                ("human", prompt_text),
            ]
        )
        self.evaluation_chain = self.evaluation_prompt | self.llm.with_structured_output(self.output_schema)

    async def evaluate_response(
        self, scenario_text: str, bot_response: str, expected_category: str, expected_response: str
    ) -> EvaluationScoreOutput | EvaluationBinaryOutput:
        """Evaluate a single bot response"""
        try:
            result = await self.evaluation_chain.ainvoke(
                {
                    "scenario": scenario_text or "",
                    "actual_response": bot_response,
                    "expected_category": expected_category or "",
                    "expected_response": expected_response or "",
                }
            )
            return result
        except Exception as e:
            logger.exception(f"Evaluation failed: {e}")
            return self.output_schema.error(f"Evaluation failed: {str(e)}")

    async def evaluate_dataset(
        self,
        csv_file: str,
        experiment_id: str,
        api_client: OCSAPIClient,
        input_column: str = "input",
        scenario_column: str = None,
        expected_category_column: str = None,
        expected_response_column: str = None,
        session_state_column: str = None,
        participant_data_column: str = None,
        history_column: str = None,
    ) -> list[EvaluationResult]:
        """Evaluate entire dataset with parallel processing"""

        # Read CSV data
        df = pd.read_csv(csv_file)

        if input_column not in df.columns:
            raise ValueError(f"Input column '{input_column}' not found in CSV")

        # Create semaphore for concurrency control
        semaphore = asyncio.Semaphore(self.max_concurrency)

        # Create tasks for parallel processing
        tasks = []
        for index, row in df.iterrows():
            task = self._evaluate_single_row(
                semaphore=semaphore,
                index=index,
                row=row,
                df=df,
                experiment_id=experiment_id,
                api_client=api_client,
                input_column=input_column,
                scenario_column=scenario_column,
                expected_category_column=expected_category_column,
                expected_response_column=expected_response_column,
                session_state_column=session_state_column,
                participant_data_column=participant_data_column,
                history_column=history_column,
                total_rows=len(df),
            )
            tasks.append(task)

        # Execute all tasks concurrently
        logger.info(f"Starting evaluation of {len(tasks)} rows with max concurrency of {self.max_concurrency}")
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter out any exceptions and log them
        evaluation_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Task {i} failed with exception: {result}", exc_info=result)
                # Create error result
                row = df.iloc[i]
                input_text = str(row[input_column])
                expected_category = self._get_optional_column(df, expected_category_column, row)
                expected_response = self._get_optional_column(df, expected_response_column, row)
                eval_result = self.output_schema.error(f"Processing failed: {str(result)}")
                error_result = EvaluationResult(
                    input_row_index=i,
                    input_text=input_text,
                    bot_response=f"ERROR: {str(result)}",
                    expected_category=expected_category or "",
                    expected_response=expected_response or "",
                    evaluation_result=eval_result.result,
                    evaluation_reasoning=eval_result.reasoning,
                    response_time=0.0,
                    session_id="",
                    timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
                    model=self.evaluator_model,
                )
                evaluation_results.append(error_result)
            else:
                evaluation_results.append(result)

        return evaluation_results

    async def _evaluate_single_row(
        self,
        semaphore: asyncio.Semaphore,
        index: int,
        row: pd.Series,
        df: pd.DataFrame,
        experiment_id: str,
        api_client: OCSAPIClient,
        input_column: str,
        scenario_column: str,
        expected_category_column: str,
        expected_response_column: str,
        session_state_column: str,
        participant_data_column: str,
        history_column: str,
        total_rows: int,
    ) -> EvaluationResult:
        """Evaluate a single row with semaphore control"""
        async with semaphore:
            input_text = str(row[input_column])
            scenario_text = _get_optional_column(df, scenario_column, row)
            expected_category = _get_optional_column(df, expected_category_column, row)
            expected_response = _get_optional_column(df, expected_response_column, row)
            session_state = _get_optional_column(df, session_state_column, row, json.loads) or {}
            participant_data = _get_optional_column(df, participant_data_column, row, json.loads) or {}
            history_data = _get_optional_column(df, history_column, row)
            if history_data:
                history_data = _parse_history_data(history_data)

            logger.debug(f"Processing row {index + 1}/{total_rows}: {input_text[:50]}...")

            try:
                # Get bot response
                bot_response, session_id, response_time = await api_client.get_bot_response(
                    experiment_id,
                    input_text,
                    session_data={**session_state, "evaluation_row": index},
                    participant_data=participant_data,
                    history_data=history_data,
                )

                # Evaluate response using the new template format
                evaluation = await self.evaluate_response(
                    scenario_text, bot_response, expected_category, expected_response
                )

                result = EvaluationResult(
                    input_row_index=index,
                    input_text=input_text,
                    bot_response=bot_response,
                    expected_category=expected_category or "",
                    expected_response=expected_response or "",
                    evaluation_result=evaluation.result,
                    evaluation_reasoning=evaluation.reasoning,
                    response_time=response_time,
                    session_id=session_id,
                    timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
                    model=self.evaluator_model,
                )

                logger.debug(f"Row {index + 1} completed - Result: {evaluation.result}")
                return result

            except Exception as e:
                logger.error(f"Error processing row {index + 1}: {e}")
                eval_result = self.output_schema.error(f"Processing failed: {str(e)}")
                result = EvaluationResult(
                    input_row_index=index,
                    input_text=input_text,
                    bot_response=f"ERROR: {str(e)}",
                    expected_category=expected_category or "",
                    expected_response=expected_response or "",
                    evaluation_result=eval_result.result,
                    evaluation_reasoning=eval_result.reasoning,
                    response_time=0.0,
                    session_id="",
                    timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
                    model=self.evaluator_model,
                )
                return result

    def save_results(self, results: list[EvaluationResult], output_file: str):
        """Save evaluation results to CSV"""
        df = pd.DataFrame([asdict(result) for result in results])
        df.to_csv(output_file, index=False)
        logger.info(f"Results saved to {output_file}")

        # Print summary statistics
        logger.info("Evaluation Summary:")

        if self.mode == "score":
            avg_score = df["evaluation_result"].mean()
            logger.info(f"  Average result: {avg_score:.2f}/10")
        elif self.mode == "binary":
            df["evaluation_result"].value_counts()
            counts = df["evaluation_result"].value_counts(dropna=False).reindex([True, False], fill_value=0)
            logger.info(f"  Result counts: True: {counts[True]}, False: {counts[False]}")

        avg_response_time = df["response_time"].mean()
        logger.info(f"  Average Response Time: {avg_response_time:.2f}s")
        logger.info(f"  Total Evaluations: {len(results)}")


def _parse_history_data(history_data):
    if not re.search(r'"role"\s*:', history_data):
        # assume it's just a message
        return [{"role": "assistant", "content": history_data}]

    for data in [history_data, history_data.replace("\n", " ")]:
        with contextlib.suppress(json.decoder.JSONDecodeError):
            history_data = json.loads(data)
            break

    if isinstance(history_data, dict):
        if {"role", "content"} - set(history_data):
            raise Exception("Malformed history data. 'role' and 'content' keys are required.")
        history_data = [history_data]
    else:
        for row in history_data:
            if not isinstance(row, dict):
                raise Exception("Malformed history data. Each item must be a dictionary.")
            if {"role", "content"} - set(row):
                raise Exception("Malformed history data. 'role' and 'content' keys are required.")
    return history_data


def _get_optional_column(df, column_name, row, converter=str):
    return converter(row[column_name]) if column_name and column_name in df.columns else None

async def main():
    """Main function"""
    parser = argparse.ArgumentParser(description="Evaluate bot responses using OCS API")
    parser.add_argument("--csv", required=True, help="Path to CSV file with test data")
    parser.add_argument("--experiment-id", required=True, help="Experiment/chatbot ID")
    parser.add_argument("--api-key", required=True, help="OCS API key")
    parser.add_argument("--base-url", default="https://chatbots.dimagi.com", help="OCS base URL")
    parser.add_argument("--input-column", default="Input from the partcipant", help="CSV column name for input text")
    parser.add_argument(
        "--scenario-column",
        default="Scenario text",
        required=False,
        help="CSV column name for scenario description",
    )
    parser.add_argument(
        "--expected-category-column",
        default="Response category",
        required=False,
        help="CSV column name for expected category (Correct, Incorrect, etc.)",
    )
    parser.add_argument(
        "--expected-response-column",
        default="Expected Response",
        required=False,
        help="CSV column name for expected response text",
    )
    parser.add_argument(
        "--session-data-column", default="session_data", required=False, help="CSV column name for session data (JSON)"
    )
    parser.add_argument(
        "--participant-data-column",
        default="participant_data",
        required=False,
        help="CSV column name for participant data (JSON)",
    )
    parser.add_argument(
        "--history-column", default="History", required=False, help="Previous session history data (JSON)"
    )
    parser.add_argument("--output", default="evaluation_results.csv", help="Output CSV file")
    parser.add_argument("--evaluator-model", default="gpt-4.1-mini", help="LLM model for evaluation")
    parser.add_argument("--custom-prompt", help="Custom evaluation prompt")
    parser.add_argument("--eval-mode", choices=["score", "binary"], help="Evaluation Mode")
    parser.add_argument("--max-concurrency", type=int, default=10, help="Maximum number of concurrent evaluations")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    parser.add_argument("--validate", action="store_true", help="Validate the dataset. This won't actually run the evals.")

    args = parser.parse_args()

    logger.setLevel(logging.DEBUG if args.verbose else logging.INFO)

    if args.validate:
        df = pd.read_csv(args.csv)

        if args.input_column not in df.columns:
            raise ValueError(f"Input column '{args.input_column}' not found in CSV")

        for index, row in df.iterrows():
            try:
                str(row[args.input_column])
                _get_optional_column(df, args.scenario_column, row)
                _get_optional_column(df, args.expected_category_column, row)
                _get_optional_column(df, args.expected_response_column, row)
                _get_optional_column(df, args.session_data_column, row, json.loads) or {}
                _get_optional_column(df, args.participant_data_column, row, json.loads) or {}
                history_data = _get_optional_column(df, args.history_column, row)
                if history_data:
                    _parse_history_data(history_data)
            except Exception as e:
                print(f"Error with row {index}: {e}")
        return

    # Initialize evaluator
    evaluator = BotEvaluator(
        evaluator_model=args.evaluator_model,
        evaluation_mode=args.eval_mode,
        custom_prompt=args.custom_prompt,
        max_concurrency=args.max_concurrency,
    )

    # Initialize API client
    async with OCSAPIClient(args.base_url, args.api_key) as api_client:
        # Run evaluation
        results = await evaluator.evaluate_dataset(
            csv_file=args.csv,
            experiment_id=args.experiment_id,
            api_client=api_client,
            input_column=args.input_column,
            scenario_column=args.scenario_column,
            expected_category_column=args.expected_category_column,
            expected_response_column=args.expected_response_column,
            session_state_column=args.session_data_column,
            participant_data_column=args.participant_data_column,
            history_column=args.history_column,
        )

        # Save results
        evaluator.save_results(results, args.output)


if __name__ == "__main__":
    asyncio.run(main())
