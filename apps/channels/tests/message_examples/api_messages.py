import json


def text_message(experiment_public_id):
    return json.dumps({"experiment_id": experiment_public_id, "message": "Hi there"})
