import json

from anthropic import Anthropic
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.teams.decorators import login_and_team_required


@require_POST
@login_and_team_required
@csrf_exempt
def help(request, team_slug: str):
    user_query = json.loads(request.body)["query"]
    return JsonResponse({"response": AiHelper.code_completion(user_query, "")})


class AiHelper:
    """
    Can use some factory method to determine the correct function to call when we have more
    """

    @staticmethod
    def code_completion(user_query, current_context) -> str:
        """
        current_context should consist of
        - existing code
        - Available methods and how to use them
        """

        system_prompt = """
            You are an expert python coder. You will be asked to generate or update code in a sandboxed environment,
            using the restricted python library.
            You must define a main function, which takes input as a string and always return a string.         

            def main(input: str, **kwargs) -> str:
                return input


            Some definitions that you need to know about:
            - Participant data: A python dictionary with participant's data.
            - Temorary state: A python dictionary.

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
                This will override any existing data for the key.
            ```
            
            Return only the Python code and nothing else. Do not enclose it in triple quotes or have any other
            explanations in the response.
        """
        client = Anthropic(api_key=settings.API_HELPER_API_KEY)
        messages = [
            {"role": "user", "content": user_query},
            {"role": "assistant", "content": "def main(input: str, **kwargs) -> str:"},
        ]

        response = client.messages.create(
            system=system_prompt, model="claude-3-sonnet-20240229", messages=messages, max_tokens=1000
        )

        return f"def main(input: str, **kwargs) -> str:{response.content[0].text}"
