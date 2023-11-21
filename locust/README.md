# Locust Scripts

This folder contains scripts for testing chatbots.

## Run

See https://docs.locust.io/en/latest/running-without-web-ui.html

With password in env:
```shell
export CHATBOTS_PASSWORD=xyz
export CHATBOTS_USERNAME=me@test.com
locust --headless --users 1 --spawn-rate 1 -H http://localhost:8000 \
  --team dimagi \
  --experiment 152 \
  --transcripts transcripts.csv
  --locustfile locustfile.py,step_load.py
```

With 1password:
```shell
locust ... --password=`op read "op://Private/OCS Login/password" --account dimagi.1password.com`
```

## Custom Args:

```shell
  --username USERNAME           Chatbots username (env CHATBOTS_PASSWORD)
  --password PASSWORD           Chatbots password (env CHATBOTS_PASSWORD)
  --team TEAM                   Chatbots team (env CHATBOTS_TEAM)
  --experiment EXPERIMENT       Experiment ID (env CHATBOTS_EXPERIMENT)
  --transcripts TRANSCRIPTS     Path to transcripts CSV file
  --min-messages MIN_MESSAGES   Min messages per transcript (optional)
```

## Debugging

Add breakpoints in file and then run:

```shell
python locustfile.py
```
