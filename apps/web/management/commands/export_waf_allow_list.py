from django.core.management import BaseCommand
from jinja2 import Environment

from apps.web.waf import get_all_waf_patterns

OUTPUT_TEMPLATE = """
{{kind.header}}
{{kind.name}} = [
{%- for regex in patterns %}
    r"{{regex}}",
{%- endfor %}
]
"""


class Command(BaseCommand):
    help = "Generate the WAF regex allow lists to copy into the ocs-deploy 'waf' module"

    def handle(self, *args, **options):
        env = Environment()
        template = env.from_string(OUTPUT_TEMPLATE)

        for kind, patterns in get_all_waf_patterns().items():
            print(template.render({"kind": kind, "patterns": patterns}))
            print()
        print("Copy the above blocks into the ocs-deploy 'waf' module.")
