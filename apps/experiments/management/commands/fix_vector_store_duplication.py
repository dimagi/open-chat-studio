import traceback

from django.core.management import BaseCommand, CommandError
from django.db import transaction

from apps.assistants.models import OpenAiAssistant
from apps.assistants.sync import push_assistant_to_openai
from apps.teams.utils import current_team


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("--team", help="The team slug", required=False)
        parser.add_argument("--assistant", help="A specific assistant id", required=False)

    def handle(self, team, assistant, **options):
        from apps.teams.models import Team

        assistants = []
        if assistant:
            assistants = [OpenAiAssistant.objects.get(assistant_id=assistant)]
        elif team:
            try:
                team = Team.objects.get(slug=team)
            except Team.DoesNotExist:
                raise CommandError(f"Team {team} does not exist.") from None
            assistants = OpenAiAssistant.objects.filter(
                team=team, working_version_id=None, tool_resources__tool_type="file_search"
            ).all()
        else:
            raise CommandError("`team` or `assistant` expected")

        with current_team(team):
            for assistant in assistants:
                with transaction.atomic():
                    try:
                        self.stdout.write(f"\n\nEvaluating {assistant} (id={assistant.id})")
                        original_resource = assistant.tool_resources.get(tool_type="file_search")
                        original_vector_store_id = original_resource.extra.get("vector_store_id")
                        original_file_ids = set(original_resource.files.values_list("external_id", flat=True))

                        for version in assistant.versions.all():
                            self.stdout.write(f"Looking at version {version.version_number} (id={version.id})")
                            tool_resource = version.tool_resources.get(tool_type="file_search")
                            if tool_resource.extra.get("vector_store_id") != original_vector_store_id:
                                self.stdout.write("Vector store ids do not match. Continuing...")
                                continue

                            self.stdout.write(f"Vector store ids match! (vector_store_id={original_vector_store_id})")
                            self.stdout.write("...clearning vector_store_id")
                            tool_resource.extra["vector_store_id"] = None
                            tool_resource.save()
                            _clear_assistant_vector_store(version)

                            self.stdout.write("...syncing version with OpenAI")
                            push_assistant_to_openai(version)
                            self.stdout.write("...syncing complete!")

                            tool_resource.refresh_from_db()
                            new_vs_id = tool_resource.extra["vector_store_id"]
                            self.stdout.write(f"Version's new vector_store_id is {new_vs_id}")

                            diff = set(tool_resource.files.values_list("external_id", flat=True))
                            self.stdout.write(f"Diff between original and new file ids: {original_file_ids - diff}")
                    except Exception as e:
                        traceback.print_exception(type(e), e, e.__traceback__)

        self.stdout.write("\nDone")


def _clear_assistant_vector_store(assistant: OpenAiAssistant):
    client = assistant.llm_provider.get_llm_service().get_raw_client()
    data = {"tool_resources": {"file_search": {"vector_store_ids": []}}}
    client.beta.assistants.update(assistant.assistant_id, **data)
