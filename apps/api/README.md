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

# Set up an experiment session with prepopulated history

data = {
    "ephemeral": False,
    "user_input": "Tell me something",
    "history" = [
        {"type": "human", "message": "Hi there"},
        {"type": "ai", "message": "Hi, how can I assist you today?"}
    ]
}

response = requests.post(
    f"https://chatbots.dimagi.com/api/experiments/{experiment_id}/sessions/new",
    data=data,
    headers=headers
)
data = response.json()
# Example response: {"session_id": "77e2b985-0931-4236-a46f-ccdced7159b4", "response": "Ok, here's something ..."}
```
If `ephemeral` is true, then the created session will be deleted and `session_id` will be None.
