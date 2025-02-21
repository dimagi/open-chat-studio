import json
import logging
import textwrap

import pydantic
from anthropic import Anthropic, AnthropicError
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.teams.decorators import login_and_team_required

logger = logging.getLogger("ocs.help")


@require_POST
@login_and_team_required
@csrf_exempt
def pipeline_generate_code(request, team_slug: str):
    user_query = json.loads(request.body)["query"]
    try:
        completion = code_completion(user_query, "")
    except (ValueError, TypeError, AnthropicError):
        logger.exception("An error occurred while generating code.")
        return JsonResponse({"error": "An error occurred while generating code."})
    return JsonResponse({"response": completion})


def code_completion(user_query, current_code, error=None, iteration_count=0) -> str:
    if iteration_count > 3:
        return current_code

    system_prompt = textwrap.dedent(
        """
        You are an expert python coder. You will be asked to generate or update code to be used as part of a
        chatbot flow. The code will be executed in a sandboxed environment using the restricted python library.
        The code must define a main function, which takes input as a string and always return a string.         

        def main(input: str, **kwargs) -> str:
            return input

        Some definitions that you need to know about:
        - Participant data: A python dictionary containing data for the current chat participant. Changes to this
            are persisted. The schema for this data is defined by the chatbot.
        - Temporary state: A python dictionary containing state that is only relevant to the current
            chatbot invocation. This contains some read only keys that are provided by the chatbot but can also
            be used to store temporary data which can be used across different nodes in the chatbot flow.
            The pre-defined keys are:
            - `user_input`: The user's input to the chatbot.
            - `outputs`: The outputs of previous nodes, keyed by the node name.
            - `attachments`: A list of attachments sent by the user. See below for the structure of an attachment.
        - Attachments:
            - `name`: The name of the file.
            - `size`: The size of the file in bytes.
            - `content_type`: The MIME type of the file.
            - `upload_to_assistant`: Whether the file should be sent to the LLM as an attachment.
            - `read_bytes()`: Reads the attachment content as bytes.
            - `read_text()`: Reads the attachment content as text.

        The available methods you can use are listed below:
        ```
        def get_participant_data() -> dict:
            Returns the current participant's data as a dictionary.

        def set_participant_data(data: dict) -> None:
            Updates the current participant's data with the provided dictionary. This will overwrite any existing
            data.

        def get_temp_state_key(key_name: str) -> str | None:
            Returns the value of the temporary state key with the given name.
            If the key does not exist, it returns `None`.

        def set_temp_state_key(key_name: str, data: Any) -> None:
            Sets the value of the temporary state key with the given name to the provided data.
            This will override any existing data for the key unless the key is read-only, in which case
            an error will be raised. Read-only keys are: `user_input`, `outputs`, `attachments`.
        ```
        
        Return only the Python code and nothing else. Do not enclose it in triple quotes or have any other
        explanations in the response.
        
        {current_code}
        {error}
    """
    )
    prompt_context = {"current_code": "", "error": ""}

    if current_code:
        prompt_context["current_code"] = f"The current function definition is:\n\n{current_code}"
    if error:
        prompt_context["error"] = f"\nThe current function has the following error. Try to resolve it:\n\n{error}"

    system_prompt = system_prompt.format(**prompt_context).strip()

    client = Anthropic(api_key=settings.API_HELPER_API_KEY)
    messages = [
        {"role": "user", "content": user_query},
        {"role": "assistant", "content": "def main(input: str, **kwargs) -> str:"},
    ]

    response = client.messages.create(
        system=system_prompt, model="claude-3-sonnet-20240229", messages=messages, max_tokens=1000
    )

    response_code = f"def main(input: str, **kwargs) -> str:{response.content[0].text}"

    from apps.pipelines.nodes.nodes import CodeNode

    try:
        CodeNode.model_validate({"code": response_code})
    except pydantic.ValidationError as e:
        error = str(e)
        return code_completion(user_query, response_code, error, iteration_count=iteration_count + 1)

    return response_code
