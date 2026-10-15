import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.web.e2e_data import STATES, State, create_e2e_data, delete_e2e_data


class Command(BaseCommand):
    help = (
        "Delete the previous Playwright E2E data, create a user and team for each scenario, "
        "and print each scenario's details as JSON. Only for DEBUG or CI."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--base-url", required=True, help="URL of the server under test. The mock LLM provider points at it."
        )
        parser.add_argument(
            "--scenario",
            action="append",
            default=[],
            metavar="NAME=STATE",
            help=f"A scenario to create, starting from STATE (one of {', '.join(STATES)}). Repeat for each scenario.",
        )

    def handle(self, *args, base_url, scenario, **options):
        if not (settings.DEBUG or settings.IS_CI):
            raise CommandError("e2e_seed creates users with a known password, so it runs only with DEBUG on or in CI.")
        scenarios = dict(_parse_scenario(value) for value in scenario)
        delete_e2e_data()
        data = {name: create_e2e_data(state=state, base_url=base_url.rstrip("/")) for name, state in scenarios.items()}
        self.stdout.write(json.dumps(data))


def _parse_scenario(value: str) -> tuple[str, State]:
    name, _, state = value.partition("=")
    if not name or state not in STATES:
        raise CommandError(f"--scenario must be NAME=STATE with STATE one of {', '.join(STATES)}, got {value!r}")
    return name, state
