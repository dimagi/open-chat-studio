The API can be used to chat to a bot. For this you'll need to create a `UserAPIKey` for your user by going to the django admin page. Be sure to keep this key somewhere safe.

### Example usage
```python
import requests

headers = {'Accept': 'application/json', 'Authorization': 'Api-Key <api-key>'}

# List experiments
response = requests.get("https://chatbots.dimagi.com/api/experiments", headers=headers)
experiments = response.json()
experiment_id = experiments[0]["experiment_id"]

# Start a conversation with the experiment bot
data = {"experiment_id": experiment_id, "message": "Hi there"}
response = requests.post("https://chatbots.dimagi.com/channels/api/incoming_message", headers=headers, data=data)
data = response.json()
```

