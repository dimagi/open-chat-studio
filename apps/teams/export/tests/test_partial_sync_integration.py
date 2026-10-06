"""One selection, served by the real serializers and imported by the real importer.

The unit tests pin each piece; this pins that the pieces agree -- in particular that nothing the
scope serves references a row the scope leaves out, which the importer otherwise surfaces as
UnresolvedForeignKey deep in a run rather than at CI time.
"""

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from apps.api.export.serializers import build_resource_serializer, build_team_serializer
from apps.chat.models import ChatMessage
from apps.evaluations.models import Evaluator
from apps.experiments.models import Experiment
from apps.files.models import File
from apps.service_providers.models import LlmProvider
from apps.teams.backends import add_user_to_team
from apps.teams.export.chatbot_scope import build_scope
from apps.teams.export.importer import Importer, mute_signals
from apps.teams.export.manifest import MANIFEST_ENTRIES, TEAM_MODEL, entry_model, scoped_queryset
from apps.teams.export.seal import load_public_key
from apps.users.models import CustomUser
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.documents import CollectionFactory, CollectionFileFactory
from apps.utils.factories.evaluations import EvaluatorFactory
from apps.utils.factories.events import ScheduledTriggerFactory, StaticTriggerFactory
from apps.utils.factories.experiment import (
    ChatMessageFactory,
    ConsentFormFactory,
    ExperimentFactory,
    ExperimentSessionFactory,
    ParticipantDataFactory,
    SourceMaterialFactory,
)
from apps.utils.factories.files import FileFactory
from apps.utils.factories.pipelines import NodeFactory, PipelineFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory
from apps.utils.factories.team import TeamFactory

pytestmark = pytest.mark.django_db


class Fixture:
    """A team with two chatbots: one selected for migration, one that must stay behind."""

    def __init__(self):
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_pem = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self.team = TeamFactory(public_key=public_pem.decode())
        self.used_provider = LlmProviderFactory(team=self.team, name="Used")
        self.unused_provider = LlmProviderFactory(team=self.team, name="Unused")
        self.collection = CollectionFactory(team=self.team, llm_provider=None, embedding_provider_model=None)
        self.collection_file = FileFactory(team=self.team)
        CollectionFileFactory(collection=self.collection, file=self.collection_file)
        self.source_material = SourceMaterialFactory(team=self.team)
        self.consent_form = ConsentFormFactory(team=self.team)

        pipeline = PipelineFactory(team=self.team)
        NodeFactory(
            pipeline=pipeline,
            llm_provider=self.used_provider,
            collection=self.collection,
            source_material=self.source_material,
        )
        self.migrating = ExperimentFactory(
            team=self.team, name="Migrating bot", pipeline=pipeline, consent_form=self.consent_form
        )
        ExperimentChannelFactory(team=self.team, experiment=self.migrating)
        StaticTriggerFactory(experiment=self.migrating)
        ScheduledTriggerFactory(experiment=self.migrating)
        session = ExperimentSessionFactory(experiment=self.migrating, team=self.team)
        self.message = ChatMessageFactory(chat=session.chat, content="Hello from the migrating bot")
        attachment = session.chat.attachments.create(tool_type="code_interpreter")
        self.attached_file = FileFactory(team=self.team)
        attachment.files.add(self.attached_file)
        ParticipantDataFactory(team=self.team, experiment=self.migrating, participant=session.participant)

        staying_pipeline = PipelineFactory(team=self.team)
        NodeFactory(pipeline=staying_pipeline, llm_provider=self.unused_provider)
        self.staying = ExperimentFactory(team=self.team, name="Staying bot", pipeline=staying_pipeline)
        staying_session = ExperimentSessionFactory(experiment=self.staying, team=self.team)
        self.staying_message = ChatMessageFactory(chat=staying_session.chat, content="Hello from the staying bot")

        self.evaluator = EvaluatorFactory(team=self.team)
        self.team.exportable_experiments.add(self.migrating)
        # Users are synced by team membership; the factories create owners outside the team.
        for user in CustomUser.objects.exclude(membership__team=self.team):
            add_user_to_team(self.team, user)


def _import_scoped_export(store) -> Importer:
    """Serialize every resource the scope serves and import it, exactly as a run would.

    Source and target share one test database, and rows such as chatbots keep their ``public_id``
    across servers, so the source team is deleted between the export and the import.
    """
    fixture = Fixture()
    public_key = load_public_key(fixture.team.public_key)
    scope = build_scope(fixture.team)

    team_row = dict(build_team_serializer()(fixture.team).data)
    exported = []
    for entry in MANIFEST_ENTRIES:
        rows = list(scoped_queryset(entry, fixture.team, scope).order_by("id"))
        serializer = build_resource_serializer(entry_model(entry.model))(
            rows, many=True, context={"team": fixture.team, "public_key": public_key}
        )
        exported.append((entry.model, serializer.data))

    importer = Importer(store, private_key=fixture.private_key)
    with mute_signals():
        # Muted so the source's file blobs stay in storage for the import to find.
        fixture.team.delete()
        importer.import_rows(TEAM_MODEL, [team_row])
        for model_label, rows in exported:
            importer.import_rows(model_label, rows)

    importer.fixture = fixture
    return importer


def test_a_scoped_export_imports_without_an_unresolved_reference(make_store, tmp_path):
    importer = _import_scoped_export(make_store(tmp_path / "team.sqlite"))
    target = importer.target_team

    assert target != importer.fixture.team
    names = set(Experiment._base_manager.filter(team=target).values_list("name", flat=True))
    assert "Migrating bot" in names


def test_the_other_chatbot_stays_behind(make_store, tmp_path):
    importer = _import_scoped_export(make_store(tmp_path / "team.sqlite"))
    target = importer.target_team

    names = set(Experiment._base_manager.filter(team=target).values_list("name", flat=True))
    assert "Staying bot" not in names
    contents = set(ChatMessage.objects.filter(chat__team=target).values_list("content", flat=True))
    assert contents == {"Hello from the migrating bot"}


def test_the_excluded_classes_stay_behind(make_store, tmp_path):
    """An evaluator referencing an experiment that was never synced would make resolve_fk raise
    mid-import; serving these empty is what keeps the run clean."""
    importer = _import_scoped_export(make_store(tmp_path / "team.sqlite"))
    assert not Evaluator.objects.filter(team=importer.target_team).exists()


def test_only_the_referenced_shared_resources_came_across(make_store, tmp_path):
    importer = _import_scoped_export(make_store(tmp_path / "team.sqlite"))
    target = importer.target_team

    assert LlmProvider.objects.filter(team=target, name="Used").exists()
    assert not LlmProvider.objects.filter(team=target, name="Unused").exists()


def test_the_chatbots_files_came_across(make_store, tmp_path):
    importer = _import_scoped_export(make_store(tmp_path / "team.sqlite"))
    target = importer.target_team

    names = set(File.objects.filter(team=target).values_list("name", flat=True))
    assert importer.fixture.collection_file.name in names
    assert importer.fixture.attached_file.name in names
