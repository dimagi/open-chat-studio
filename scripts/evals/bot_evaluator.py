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
import logging
import time
from dataclasses import asdict, dataclass
from typing import Any

import aiohttp
import pandas as pd
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class EvaluationResult:
    """Data class for evaluation results"""

    input_text: str
    bot_response: str
    evaluation_score: float
    evaluation_reasoning: str
    response_time: float
    session_id: str
    timestamp: str


class EvaluationOutput(BaseModel):
    """Pydantic model for evaluation output"""

    score: float = Field(description="Score from 1-10 where 10 is best")
    reasoning: str = Field(description="Detailed reasoning for the score")


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

    async def start_chat_session(self, experiment_id: str, session_data: dict[str, Any] = None) -> str:
        """Start a new chat session"""
        url = f"{self.base_url}/api/chat/start/"
        payload = {"chatbot_id": experiment_id, "session_data": session_data or {}}

        async with self.session.post(url, json=payload) as response:
            if response.status == 201:
                data = await response.json()
                return data["session_id"]
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
        self, experiment_id: str, input_text: str, session_data: dict[str, Any] = None
    ) -> tuple[str, str, float]:
        """Get bot response for input text"""
        start_time = time.time()

        # Start session
        session_id = await self.start_chat_session(experiment_id, session_data)

        # Send message
        task_id = await self.send_message(session_id, input_text)

        # Poll for response
        message = await self.poll_for_response(session_id, task_id)

        response_time = time.time() - start_time

        # Extract bot response
        bot_response = message.get("content", "")
        return bot_response, session_id, response_time


class BotEvaluator:
    """Main bot evaluation class"""

    def __init__(self, evaluator_model: str = "gpt-4o-mini"):
        self.evaluator_model = evaluator_model
        self.llm = ChatOpenAI(model=evaluator_model, temperature=0)
        self.parser = JsonOutputParser(pydantic_object=EvaluationOutput)

        # Default evaluation prompt
        self.evaluation_prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    """You are an expert evaluator of chatbot responses. 
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
            
            {format_instructions}""",
                ),
                ("human", "User Input: {input_text}\n\nBot Response: {bot_response}\n\nEvaluate this response."),
            ]
        )

        self.evaluation_chain = self.evaluation_prompt | self.llm | self.parser

    def set_custom_evaluation_prompt(self, prompt: str):
        """Set a custom evaluation prompt"""
        self.evaluation_prompt = ChatPromptTemplate.from_messages(
            [
                ("system", prompt + "\n\n{format_instructions}"),
                ("human", "User Input: {input_text}\n\nBot Response: {bot_response}\n\nEvaluate this response:"),
            ]
        )
        self.evaluation_chain = self.evaluation_prompt | self.llm | self.parser

    async def evaluate_response(self, input_text: str, bot_response: str) -> EvaluationOutput:
        """Evaluate a single bot response"""
        try:
            result = await self.evaluation_chain.ainvoke(
                {
                    "input_text": input_text,
                    "bot_response": bot_response,
                    "format_instructions": self.parser.get_format_instructions(),
                }
            )
            return EvaluationOutput(**result)
        except Exception as e:
            logger.exception(f"Evaluation failed: {e}")
            return EvaluationOutput(
                score=0.0,
                reasoning=f"Evaluation failed: {str(e)}",
            )

    async def evaluate_dataset(
        self,
        csv_file: str,
        experiment_id: str,
        api_client: OCSAPIClient,
        input_column: str = "input",
        expected_output_column: str = None,
        custom_prompt: str = None,
    ) -> list[EvaluationResult]:
        """Evaluate entire dataset"""

        if custom_prompt:
            self.set_custom_evaluation_prompt(custom_prompt)

        # Read CSV data
        df = pd.read_csv(csv_file)

        if input_column not in df.columns:
            raise ValueError(f"Input column '{input_column}' not found in CSV")

        results = []

        for index, row in df.iterrows():
            input_text = str(row[input_column])
            expected_output = (
                str(row[expected_output_column])
                if expected_output_column and expected_output_column in df.columns
                else None
            )

            logger.info(f"Processing row {index + 1}/{len(df)}: {input_text[:50]}...")

            try:
                # Get bot response
                bot_response, session_id, response_time = await api_client.get_bot_response(
                    experiment_id, input_text, session_data={"evaluation_row": index}
                )

                # Evaluate response
                evaluation_input = input_text
                if expected_output:
                    evaluation_input += f"\n\nExpected Output: {expected_output}"

                evaluation = await self.evaluate_response(evaluation_input, bot_response)

                result = EvaluationResult(
                    input_text=input_text,
                    bot_response=bot_response,
                    evaluation_score=evaluation.score,
                    evaluation_reasoning=evaluation.reasoning,
                    response_time=response_time,
                    session_id=session_id,
                    timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
                )

                results.append(result)

                logger.info(f"Row {index + 1} completed - Score: {evaluation.score:.1f}")

            except Exception as e:
                logger.error(f"Error processing row {index + 1}: {e}")
                result = EvaluationResult(
                    input_text=input_text,
                    bot_response=f"ERROR: {str(e)}",
                    evaluation_score=0.0,
                    evaluation_reasoning=f"Processing failed: {str(e)}",
                    response_time=0.0,
                    session_id="",
                    timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
                )
                results.append(result)

        return results

    def save_results(self, results: list[EvaluationResult], output_file: str):
        """Save evaluation results to CSV"""
        df = pd.DataFrame([asdict(result) for result in results])
        df.to_csv(output_file, index=False)
        logger.info(f"Results saved to {output_file}")

        # Print summary statistics
        avg_score = df["evaluation_score"].mean()
        avg_response_time = df["response_time"].mean()

        logger.info("Evaluation Summary:")
        logger.info(f"  Average Score: {avg_score:.2f}/10")
        logger.info(f"  Average Response Time: {avg_response_time:.2f}s")
        logger.info(f"  Total Evaluations: {len(results)}")


async def main():
    """Main function"""
    parser = argparse.ArgumentParser(description="Evaluate bot responses using OCS API")
    parser.add_argument("--csv", required=True, help="Path to CSV file with test data")
    parser.add_argument("--experiment-id", required=True, help="Experiment/chatbot ID")
    parser.add_argument("--api-key", required=True, help="OCS API key")
    parser.add_argument("--base-url", default="https://chatbots.dimagi.com", help="OCS base URL")
    parser.add_argument("--input-column", default="input", help="CSV column name for input text")
    parser.add_argument("--expected-output-column", help="CSV column name for expected output")
    parser.add_argument("--output", default="evaluation_results.csv", help="Output CSV file")
    parser.add_argument("--evaluator-model", default="gpt-4o-mini", help="LLM model for evaluation")
    parser.add_argument("--custom-prompt", help="Custom evaluation prompt")

    args = parser.parse_args()

    # Initialize evaluator
    evaluator = BotEvaluator(evaluator_model=args.evaluator_model)

    # Initialize API client
    async with OCSAPIClient(args.base_url, args.api_key) as api_client:
        # Run evaluation
        results = await evaluator.evaluate_dataset(
            csv_file=args.csv,
            experiment_id=args.experiment_id,
            api_client=api_client,
            input_column=args.input_column,
            expected_output_column=args.expected_output_column,
            custom_prompt=args.custom_prompt,
        )

        # Save results
        evaluator.save_results(results, args.output)


if __name__ == "__main__":
    asyncio.run(main())
