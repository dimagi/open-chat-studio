The API can be used to chat to a bot. For this you"ll need to create a `UserAPIKey` for your user by going to the django admin page. Be sure to keep this key somewhere safe.

# Example usage

## Start a new session

```python
import requests

headers = {"Accept": "application/json", "Content-Type": "application/json", "X-Api-Key": "<api-key>"}

# List experiments
response = requests.get("https://chatbots.dimagi.com/api/experiments", headers=headers)
experiments = response.json()
experiment_id = experiments[0]["id"]

# Start a conversation with the experiment bot
data = {"message": "Hi there"}
response = requests.post(
    f"https://chatbots.dimagi.com/channels/api/{experiment_id}/incoming_message",
    data=data,
    headers=headers
)
reply_message = response.json()["response"]
```

## Create a new session with history

```python
import requests

headers = {"Accept": "application/json", "Content-Type": "application/json", "X-Api-Key": "<api-key>"}

experiment_id = "experiment_id"
data = {
    "experiment": experiment_id,
    "messages": [
        {"type": "ai", "message": "hi"},
        {"type": "human", "message": "hello"},
    ],
}

response = requests.post("https://chatbots.dimagi.com/api/sessions", data=data, headers=headers)
session_id = response.json()["id"]

# Update the session with a new message
data = {"message": "Hi there", "session": session_id}
response = requests.post(
    f"https://chatbots.dimagi.com/channels/api/{experiment_id}/incoming_message",
    data=data,
    headers=headers
)
reply_message = response.json()["response"]
```
