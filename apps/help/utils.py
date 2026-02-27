import inspect
import textwrap

from apps.pipelines.nodes.base import PipelineState
from apps.pipelines.nodes.context import NodeContext
from apps.pipelines.nodes.nodes import CodeNode
from apps.pipelines.repository import InMemoryPipelineRepository

PYTHON_NODE_HELP_PROMPT = textwrap.dedent(
    """
    You are an expert python coder. You will be asked to generate or update code to be used as part of a
    chatbot flow. The code will be executed in a sandboxed environment using the restricted python library.
    The code must define a main function, which takes input as a string and always return a string.

    ```
    def main(input: str, **kwargs) -> str:
        return input
    ```

    All code must be contained within the main function. This includes imports and any other function definitions:

    ```
    def main(input: str, **kwargs) -> str:
        import json

        def get_json_key(data, key):
            return json.loads(data)[key]

        return get_json_key('{{"a_key": 1}}', "a_key")
    ```

    The code is executed in a restricted Python environment. There are no 3rd party libraries installed.

    Some definitions that you need to know about:
    - Participant data: A python dictionary containing data for the current chat participant. Changes to this
        are persisted. The schema for this data different between projects.
    - Temporary state: A python dictionary containing state that is only relevant to the current message.
        This contains some read only keys that are provided by the chatbot but can also
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

    There are also a set of custom functions available in the global scope:
    ```
    {utility_functions}
    ```

    Return only the Python code and nothing else. Do not enclose it in triple quotes or have any other
    explanations in the response.

    {current_code}
    {error}
"""
)


def get_python_node_coder_prompt(current_code: str, error: str) -> str:
    from apps.pipelines.nodes.nodes import DEFAULT_FUNCTION

    if current_code == DEFAULT_FUNCTION:
        current_code = ""

    prompt_context = {"current_code": "", "error": "", "utility_functions": get_python_node_functions()}

    if current_code:
        prompt_context["current_code"] = f"The current function definition is:\n\n{current_code}"
    if error:
        prompt_context["error"] = f"\nThe current function has the following error. Try to resolve it:\n\n{error}"

    return PYTHON_NODE_HELP_PROMPT.format(**prompt_context).strip()


def get_python_node_functions():
    node = CodeNode(name="test", node_id="123", django_node=None, code="")
    node._repo = InMemoryPipelineRepository()
    mock_state = PipelineState(outputs={}, experiment_session=None)
    res = node._get_custom_functions(state=mock_state, context=NodeContext(mock_state), output_state=mock_state)
    function_docs = filter(None, [extract_function_signature(name, obj) for name, obj in res.items()])
    return "\n".join(function_docs)


def extract_function_signature(name, obj) -> str | None:
    if not callable(obj):
        return None

    sig = inspect.signature(obj)
    func_signature = f"def {name}{sig}:"

    docstring = inspect.getdoc(obj)
    if docstring:
        docstring = textwrap.indent(docstring, "    ").strip()
        docstring_formatted = f'    """{docstring}"""'
        func_def = f"{func_signature}\n{docstring_formatted}\n"
    else:
        func_def = f"{func_signature}\n    pass\n"

    return func_def
