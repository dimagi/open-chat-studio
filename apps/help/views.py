import json
import logging
import textwrap

import pydantic
from anthropic import Anthropic, AnthropicError
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.pipelines.nodes.nodes import DEFAULT_FUNCTION
from apps.teams.decorators import login_and_team_required

logger = logging.getLogger("ocs.help")


@require_POST
@login_and_team_required
@csrf_exempt
def pipeline_generate_code(request, team_slug: str):
    body = json.loads(request.body)
    user_query = body["query"]
    current_code = body["context"]
    try:
        completion = code_completion(user_query, current_code)
    except (ValueError, TypeError, AnthropicError):
        logger.exception("An error occurred while generating code.")
        return JsonResponse({"error": "An error occurred while generating code."})
    return JsonResponse({"response": completion})


def code_completion(user_query, current_code, error=None, iteration_count=0) -> str:
    if iteration_count > 3:
        return current_code

    if current_code == DEFAULT_FUNCTION:
        current_code = ""

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
        - Tags: Tags can be attached to individual messages or to the chat session. Tags are used by bot
            administrators to analyse bot usage.

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

        def get_session_state_key(key_name: str) -> str | None:
            Returns the value of the session state's key with the given name.
            If the key does not exist, it returns `None`.

        def set_session_state_key(key_name: str, data: Any) -> None:
            Sets the value of the session state's key with the given name to the provided data.
            This will override any existing data.

        def get_selected_route(router_node_name: str) -> str | None:
            Returns the route selected by a specific router node with the given name.
            If the node does not exist or has no route defined, it returns `None`.

        def get_node_path(node_name: str) -> list | None:
            Returns a list containing the sequence of nodes leading to the target node.
            If the node is not found in the pipeline path, returns a list containing
            only the specified node name.

        def get_all_routes() -> dict:
            Returns a dictionary containing all routing decisions in the pipeline.
            The keys are the node names and the values are the routes chosen by each node.

        def add_message_tag(tag_name: str):
            Adds a tag to the output message.

        def add_session_tag(tag_name: str):
            Adds the tag to the chat session.

        def get_node_output(node_name: str) -> Any:
            Returns the output of the specified node if it has been executed.
            If the node has not been executed, it returns `None`.

        def abort_pipeline(message, tag_name: str = None) -> None:
            Calling this will terminate the pipeline execution. No further nodes will get executed in
            any branch of the pipeline graph.

            The message provided will be used to notify the user about the reason for the termination.
            If a tag name is provided, it will be used to tag the output message.

        def require_node_outputs(*node_names):
            This function is used to ensure that the specified nodes have been executed and their outputs
            are available in the pipeline's state. If any of the specified nodes have not been executed,
            the node will not execute and the pipeline will wait for the required nodes to complete.

            This should be called at the start of the main function.

        def wait_for_next_input():
            Advanced utility that will abort the current execution. This is similar to `require_node_outputs` but
            used where some node outputs may be optional.

            Example:
            def main(input, **kwargs):
                a = get_node_output("a")
                b = get_node_output("b")
                if a is None and b is None:
                    wait_for_next_input()
                # do something with a or b
        ```

        HTTP Client:
        An `http` global variable is available for making secure HTTP requests to external APIs. It has
        built-in security features including SSRF prevention (blocks private IPs and localhost), size limits,
        timeout clamping, and automatic retries with exponential backoff.

        Available methods (all accept parameters compatible with the `requests` library such as `headers`,
        `params`, `json`, `data`, `timeout`, `files`, and `auth`):
        ```
        http.get(url, **kwargs) -> dict
        http.post(url, **kwargs) -> dict
        http.put(url, **kwargs) -> dict
        http.patch(url, **kwargs) -> dict
        http.delete(url, **kwargs) -> dict
        ```

        All methods return a dictionary with the following keys:
        - `status_code`: The HTTP status code.
        - `headers`: The response headers.
        - `text`: The response body as text (always present).
        - `json`: The parsed JSON response body, or `None` if the response is not JSON.
        - `is_success`: `True` if the status code is in the 200-299 range.
        - `is_error`: `True` if the status code is 400 or above.

        The HTTP client never raises exceptions. All error information is contained in the response dict.
        Always check `response["status_code"]` or the `is_success` / `is_error` keys before processing
        the response.

        Authentication credentials can be injected automatically from team Authentication Providers via the
        `auth` parameter. The value must be the name of the authentication provider. Example:
        ```
        response = http.get("https://api.example.com/data", auth="my_provider")
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

    client = Anthropic(api_key=settings.AI_HELPER_API_KEY)
    messages = [
        {"role": "user", "content": user_query},
        {"role": "assistant", "content": "def main(input: str, **kwargs) -> str:"},
    ]

    response = client.messages.create(
        system=system_prompt, model=settings.AI_HELPER_API_MODEL, messages=messages, max_tokens=1000
    )

    response_code = f"def main(input: str, **kwargs) -> str:{response.content[0].text}"

    from apps.pipelines.nodes.nodes import CodeNode

    try:
        CodeNode.model_validate({"code": response_code, "name": "code", "node_id": "code", "django_node": None})
    except pydantic.ValidationError as e:
        error = str(e)
        return code_completion(user_query, response_code, error, iteration_count=iteration_count + 1)

    return response_code
