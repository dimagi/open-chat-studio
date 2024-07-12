import pytest

from apps.experiments.tasks import get_prompt_builder_response
from apps.utils.langchain import build_fake_llm_service


@pytest.mark.django_db()
def test_get_prompt_builder_response(team_with_users):
    llm_service = build_fake_llm_service(["I am very nice"], [5, 30])
    data = {
        "model": "gpt-3.5-turbo",
        "temperature": 0.5,
        "inputFormatter": None,
        "messages": [
            {
                "author": "User",
                "message": "Hello",
            }
        ],
        "prompt": "Be nice",
    }
    user = team_with_users.members.first()
    response = get_prompt_builder_response(llm_service, team_with_users.id, user, data)
    assert response == {
        "message": "I am very nice",
        "input_tokens": 5,
        "output_tokens": 30,
    }
