import csv
import logging
import random
import time
from collections import defaultdict

from bs4 import BeautifulSoup
from locust.exception import InterruptTaskSet

from locust import HttpUser, between, events, run_single_user, task


@events.init_command_line_parser.add_listener
def _(parser):
    parser.add_argument("--username", env_var="CHATBOTS_USERNAME", help="Chatbots username")
    parser.add_argument("--password", env_var="CHATBOTS_PASSWORD", is_secret=True, default="Chatbots password")
    parser.add_argument("--team", required=True, env_var="CHATBOTS_TEAM", default="dimagi", help="Chatbots team")
    parser.add_argument("--experiment", required=True, type=int, env_var="CHATBOTS_EXPERIMENT", help="Experiment ID")
    parser.add_argument("--transcripts", required=True, help="Path to transcripts CSV file")
    parser.add_argument("--min-messages", type=int, help="Min messages per transcript", default=5)


def _load_transcripts(path, min_messages):
    # For a better way to do this in future
    # see https://github.com/locustio/locust/blob/master/examples/custom_messages.py
    with open(path) as f:
        reader = csv.DictReader(f)
        messages = list(reader)

    transcripts = defaultdict(list)
    for message in messages:
        transcripts[message["chat_id"]].append(message["message"])

    return {chat_id: messages for chat_id, messages in transcripts.items() if len(messages) >= min_messages}


TRANSCRIPTS = {}


@events.init.add_listener
def _(environment, **kw):
    min_messages = environment.parsed_options.min_messages
    TRANSCRIPTS.update(_load_transcripts(environment.parsed_options.transcripts, min_messages))
    logging.info("Loaded %s transcripts with %s messages or more", len(TRANSCRIPTS), min_messages)


class BotUser(HttpUser):
    wait_time = between(1, 5)

    def _get_csrf(self, url):
        response = self.client.get(url)
        response.raise_for_status()
        return response.cookies["csrftoken"]

    @task
    def run_bot_session(self):
        team = self.environment.parsed_options.team
        experiment_id = self.environment.parsed_options.experiment

        chat_id = random.choice(list(TRANSCRIPTS))
        messages = TRANSCRIPTS[chat_id]
        logging.debug("===================== %s: %s =====================", chat_id, len(messages))
        csrftoken = self._get_csrf(f"/a/{team}/experiments/e/{experiment_id}/")
        with self.client.post(
            f"/a/{team}/experiments/e/{experiment_id}/start_authed_web_session/",
            headers={"X-CSRFToken": csrftoken},
            cookies={"csrftoken": csrftoken},
            allow_redirects=False,
            catch_response=True,
        ) as response:
            if response.status_code != 302:
                raise InterruptTaskSet("Start session failed!")

        session_url = response.headers["location"]
        response = self.client.get(session_url)
        logging.debug(">>>> AI: %s", self._get_bot_response(response))
        for message in messages:
            logging.debug(">>>> HUMAN: %s", message)
            response = self.client.post(
                f"{session_url}message/",
                data={"message": message},
                headers={"X-CSRFToken": csrftoken},
                cookies={"csrftoken": csrftoken},
            )
            bot_response = self._get_bot_response(response)
            logging.debug(">>>> AI: %s", bot_response)

    def _get_bot_response(self, response):
        soup = BeautifulSoup(response.content, features="html.parser")
        system_msg = soup.find("div", class_="chat-message-system")
        if system_msg and system_msg.has_attr("hx-get"):
            time.sleep(1)
            get_response_url = system_msg["hx-get"]
            response = self.client.get(get_response_url)
            return self._get_bot_response(response)

        msg_tag = soup.find("div", class_="message-contents")
        if msg_tag:
            para = msg_tag.find_next("p")
            if "text-error" in para.get("class", ""):
                response.failure(f"Error from bot: '{para.string}'")
            return para.string

        response.failure("Could not find bot response")
        return None

    def on_start(self):
        self.client.headers = {"Origin": self.host}
        csrftoken = self._get_csrf("/accounts/login/")
        with self.client.post(
            "/accounts/login/",
            headers={"X-CSRFToken": csrftoken},
            data={
                "login": self.environment.parsed_options.username,
                "password": self.environment.parsed_options.password,
            },
            allow_redirects=False,
            catch_response=True,
        ) as response:
            if response.status_code != 302:
                raise InterruptTaskSet("Login failed!")


if __name__ == "__main__":
    run_single_user(BotUser)
