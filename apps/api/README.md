The API can be used to chat to a bot. For this you'll need to create a `UserAPIKey` for your user by going to the django admin page. Be sure to keep this key somewhere safe.

### Example usage
```python
import requests

headers = {'Accept': 'application/json', 'Content-Type': 'application/json', 'X-Api-Key': <api-key>}

# List experiments
response = requests.get("https://chatbots.dimagi.com/api/experiments", headers=headers)
experiments = response.json()
experiment_id = experiments[0]["experiment_id"]

# Start a conversation with the experiment bot
data = {"message": "Hi there"}
response = requests.post(
    f"https://chatbots.dimagi.com/channels/api/{experiment_id}/incoming_message",
    data=data,
    headers=headers
)
data = response.json()

# Set up an experiment session with prepopulated history, or use an existing one

data = {
    "session_id": "", # Optional: The session to use
    "user_input": "Tell me something",
    "history": [ # Optional: History that you want to have in your session
        {"type": "human", "message": "Hi there"},
        {"type": "ai", "message": "Hi, how can I assist you today?"}
    ]
}
Please note that `session_id` and `history` cannot be specified in the same request. Whenever `session_id` is not
specified, a new session with `history` will be created.

response = requests.post(
    f"https://chatbots.dimagi.com/api/experiments/{experiment_id}/sessions/new",
    data=data,
    headers=headers
)
data = response.json()
# Example response: {"session_id": "77e2b985-0931-4236-a46f-ccdced7159b4", "response": "Ok, here's something ..."}
```
