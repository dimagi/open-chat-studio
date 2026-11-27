"""The version spec framework, traversed on the test-only versioned models."""

import logging
import uuid

import pytest
from django.apps import apps
from django.db import connection
from django.db.models.signals import post_save
from django.test.utils import CaptureQueriesContext

from apps.experiments.versioning import (
    Archiving,
    Operation,
    OwnedSet,
    ParamReference,
    Reference,
    Strategy,
    VersionSpec,
    VersionSpecError,
)
from apps.utils.tests.versioning_helpers import assert_family, assert_snapshot

pytestmark = [pytest.mark.django_db(), pytest.mark.usefixtures("isolated_spec_registry")]

WIDGET = "utils_tests.VersionedWidget"
PART = "utils_tests.VersionedPart"
WIDGET_IDENTITY = frozenset(
    {"id", "token", "created_at", "updated_at", "working_version", "version_number", "is_archived"}
)
PART_IDENTITY = frozenset({"id", "created_at", "updated_at", "working_version", "is_archived"})


@pytest.fixture()
def models():
    return apps.get_model(WIDGET), apps.get_model(PART)


def bind_widget(**members):
    """Declare a widget spec with `members`, binding it for the rest of the test."""
    meta = type("Meta", (), {"model": WIDGET, "identity": WIDGET_IDENTITY, "regenerated": {"token": uuid.uuid4}})
    return type("WidgetSpec", (VersionSpec,), {"Meta": meta, **members})


def bind_part():
    meta = type("Meta", (), {"model": PART, "identity": PART_IDENTITY})
    return type("PartSpec", (VersionSpec,), {"Meta": meta})


def make_widget(models, **kwargs):
    widget_model, part_model = models
    kwargs.setdefault("part", part_model.objects.create(name="part"))
    kwargs.setdefault("live_part", part_model.objects.create(name="live"))
    return widget_model.objects.create(name="widget", note="first draft", **kwargs)


def declared_references():
    return {"part": Reference(Strategy.NEW_VERSION), "live_part": Reference(Strategy.LIVE_REFERENCE)}


class TestDeclaration:
    def test_row_fields_are_every_concrete_field_outside_identity_and_the_members(self):
        spec = bind_widget(**declared_references())()

        assert {field.name for field in spec.row_fields} == {"name", "note", "params"}
        assert spec.travelling_fields == {"name", "note", "params", "part", "live_part"}

    def test_members_keep_class_body_order(self):
        spec_cls = bind_widget(live_part=Reference(Strategy.LIVE_REFERENCE), part=Reference(Strategy.NEW_VERSION))

        assert [member.name for member in spec_cls.members] == ["live_part", "part"]

    def test_a_model_has_one_spec(self):
        bind_widget()

        with pytest.raises(VersionSpecError, match="already has a VersionSpec"):
            bind_widget()

    def test_a_spec_names_the_model_it_binds(self):
        with pytest.raises(VersionSpecError, match=r"Meta\.model must name the model"):
            type("Unbound", (VersionSpec,), {"Meta": type("Meta", (), {"identity": frozenset()})})

    def test_for_model_finds_the_bound_spec_or_nothing(self, models):
        widget_model, part_model = models
        spec_cls = bind_widget()

        assert isinstance(VersionSpec.for_model(widget_model), spec_cls)
        assert VersionSpec.for_model(part_model) is None

    def test_a_reference_on_a_one_to_one_field_is_rejected(self):
        """The clone is inserted holding the working target, which a one-to-one constraint refuses."""
        member = Reference(Strategy.PRIVATE_CLONE)
        member.name = "action"

        findings = member.check(apps.get_model("events", "StaticTrigger"))

        assert [code for code, _message in findings] == ["E001"]
        assert "one-to-one" in findings[0][1]


class TestPublish:
    def test_numbers_the_version_and_advances_the_working_row(self, models):
        bind_widget(**declared_references())
        working = make_widget(models)

        first = working.create_new_version()
        second = working.create_new_version()

        assert (first.version_number, second.version_number, working.version_number) == (1, 2, 3)
        assert_family(second, working)
        assert_snapshot(working, second, changed={"part"})

    def test_the_version_number_is_taken_from_the_database_row(self, models):
        """Another publisher may have advanced the row since this instance loaded it."""
        bind_widget(**declared_references())
        working = make_widget(models)
        widget_model, _part_model = models
        widget_model.objects.filter(pk=working.pk).update(version_number=3)

        version = working.create_new_version()
        working.refresh_from_db()

        assert (version.version_number, working.version_number) == (3, 4)

    def test_the_working_row_is_locked_before_it_is_cloned(self, models):
        """The lock holds the row still for the members, and the version number comes from that read."""
        bind_widget(**declared_references())
        working = make_widget(models)

        with CaptureQueriesContext(connection) as queries:
            working.create_new_version()

        assert "FOR UPDATE" in statements(queries)[0]

    def test_regenerates_identity_before_the_first_save_and_leaves_the_working_row_alone(self, models):
        bind_widget(**declared_references())
        working = make_widget(models)
        token = working.token

        version = working.create_new_version()
        working.refresh_from_db()

        assert version.token != token
        assert working.token == token

    @pytest.mark.parametrize(
        ("strategy", "expectation"),
        [
            pytest.param(Strategy.NEW_VERSION, "new-version", id="new-version"),
            pytest.param(Strategy.REUSE_UNCHANGED, "new-version", id="reuse-unchanged-makes-the-first-version"),
            pytest.param(Strategy.LIVE_REFERENCE, "same-row", id="live-reference"),
            pytest.param(Strategy.PRIVATE_CLONE, "private-clone", id="private-clone"),
        ],
    )
    def test_each_strategy_carries_the_reference(self, models, strategy, expectation):
        bind_widget(part=Reference(strategy), live_part=Reference(Strategy.LIVE_REFERENCE))
        working = make_widget(models)

        version = working.create_new_version()

        if expectation == "same-row":
            assert version.part_id == working.part_id
        elif expectation == "new-version":
            assert version.part.working_version_id == working.part_id
            assert version.part.name == working.part.name
        else:
            assert version.part_id != working.part_id
            assert version.part.working_version_id is None
            assert version.part.name == working.part.name

    def test_reuse_unchanged_reuses_the_latest_version_until_the_target_changes(self, models):
        bind_widget(part=Reference(Strategy.REUSE_UNCHANGED), live_part=Reference(Strategy.LIVE_REFERENCE))
        working = make_widget(models)

        first = working.create_new_version()
        second = working.create_new_version()
        working.part.name = "renamed"
        working.part.save()
        third = working.create_new_version()

        assert first.part_id == second.part_id
        assert third.part_id != second.part_id
        assert working.part.versions.count() == 2

    def test_a_null_reference_stays_null(self, models):
        bind_widget(**declared_references())
        working = make_widget(models, part=None)

        assert working.create_new_version().part_id is None

    def test_a_member_reading_what_no_earlier_member_provided_leaves_no_row_behind(self, models):
        bridge_calls = []
        bind_widget(
            children=OwnedSet(via=lambda *args: bridge_calls.append(args), accessors=("versions",), reads=("part",)),
            part=Reference(Strategy.NEW_VERSION, provides="part"),
            live_part=Reference(Strategy.LIVE_REFERENCE),
        )
        working = make_widget(models)

        with pytest.raises(VersionSpecError, match="children reads \\['part'\\]"):
            working.create_new_version()

        working.refresh_from_db()
        assert (working.versions.count(), working.version_number, bridge_calls) == (0, 1, [])

    def test_members_run_in_order_and_hooks_after_them(self, models):
        seen = {}

        def bridge(working, other, operation, context):
            seen["bridge"] = (operation, other.part_id, context["part"].pk)

        class Hooked(VersionSpec):
            class Meta:
                model = WIDGET
                identity = WIDGET_IDENTITY
                regenerated = {"token": uuid.uuid4}

            part = Reference(Strategy.NEW_VERSION, provides="part")
            live_part = Reference(Strategy.LIVE_REFERENCE)
            children = OwnedSet(via=bridge, accessors=("versions",), reads=("part",))

            def on_publish(self, working, version, *, note="hooked", **options):
                seen["hook"] = version.part_id
                version.note = note

        working = make_widget(models)

        version = Hooked().publish(working, note="from the hook")
        version.refresh_from_db()

        assert seen["bridge"] == (Operation.PUBLISH, version.part_id, version.part_id)
        assert seen["hook"] == version.part_id
        assert version.note == "from the hook"

    def test_a_failing_hook_rolls_the_whole_publish_back(self, models):
        class Failing(VersionSpec):
            class Meta:
                model = WIDGET
                identity = WIDGET_IDENTITY
                regenerated = {"token": uuid.uuid4}

            part = Reference(Strategy.NEW_VERSION)
            live_part = Reference(Strategy.LIVE_REFERENCE)

            def on_publish(self, working, version, **options):
                raise RuntimeError("no")

        working = make_widget(models)

        with pytest.raises(RuntimeError):
            working.create_new_version()

        working.refresh_from_db()
        assert (working.versions.count(), working.part.versions.count(), working.version_number) == (0, 0, 1)


class TestCopy:
    def test_a_copy_is_a_new_working_row_numbered_from_one(self, models):
        bind_widget(**declared_references())
        working = make_widget(models, version_number=4)

        copy = working.create_new_version(is_copy=True)
        working.refresh_from_db()

        assert copy.working_version_id is None
        assert (copy.version_number, working.version_number) == (1, 4)
        assert copy.token != working.token
        assert_snapshot(working, copy)

    def test_a_copy_copies_owned_and_private_references_and_keeps_the_rest(self, models):
        bind_widget(part=Reference(Strategy.NEW_VERSION, owned=True), live_part=Reference(Strategy.PRIVATE_CLONE))
        working = make_widget(models)

        copy = working.create_new_version(is_copy=True)

        assert copy.part_id != working.part_id
        assert copy.part.working_version_id is None
        assert copy.live_part_id != working.live_part_id
        assert copy.live_part.working_version_id is None

        bind_part()
        VersionSpec.registry.pop(WIDGET.lower())
        bind_widget(**declared_references())
        plain = working.create_new_version(is_copy=True)

        assert (plain.part_id, plain.live_part_id) == (working.part_id, working.live_part_id)

    def test_on_copy_runs_with_the_options(self, models):
        def on_copy(self, working, copy, *, name="unnamed", **options):
            copy.name = name

        spec_cls = bind_widget(**declared_references(), on_copy=on_copy)
        working = make_widget(models)

        copy = spec_cls().copy(working, name="the copy")
        copy.refresh_from_db()

        assert copy.name == "the copy"


class TestRevert:
    def test_row_fields_come_back_from_the_version(self, models):
        bind_widget(**declared_references())
        working = make_widget(models)
        version = working.create_new_version()
        working.name, working.note = "edited", "edited"
        working.save()

        working.revert_to_version(version)
        working.refresh_from_db()

        assert (working.name, working.note) == ("widget", "first draft")
        assert working.version_number == 2

    def test_an_unowned_reference_points_back_at_the_working_target(self, models):
        bind_widget(**declared_references())
        working = make_widget(models)
        version = working.create_new_version()
        _widget_model, part_model = models
        working.part = part_model.objects.create(name="other")
        working.save()

        working.revert_to_version(version)
        working.refresh_from_db()

        assert working.part_id == version.part.working_version_id

    def test_an_owned_reference_is_reverted_in_place(self, models):
        bind_part()
        bind_widget(part=Reference(Strategy.NEW_VERSION, owned=True), live_part=Reference(Strategy.LIVE_REFERENCE))
        working = make_widget(models)
        version = working.create_new_version()
        working.part.name = "edited"
        working.part.save()

        working.revert_to_version(version)
        working.refresh_from_db()

        assert working.part_id == version.part.working_version_id
        assert working.part.name == "part"

    def test_an_owned_reference_the_version_lacks_leaves_the_working_target_alone(self, models):
        bind_part()
        bind_widget(part=Reference(Strategy.NEW_VERSION, owned=True), live_part=Reference(Strategy.LIVE_REFERENCE))
        working = make_widget(models, part=None)
        version = working.create_new_version()
        _widget_model, part_model = models
        working.part = part_model.objects.create(name="added later")
        working.save()

        working.revert_to_version(version)
        working.refresh_from_db()

        assert working.part.name == "added later"

    def test_an_owned_reference_the_working_row_lost_cannot_be_reverted(self, models):
        bind_part()
        bind_widget(part=Reference(Strategy.NEW_VERSION, owned=True), live_part=Reference(Strategy.LIVE_REFERENCE))
        working = make_widget(models)
        version = working.create_new_version()
        working.part = None
        working.save()

        with pytest.raises(ValueError, match="Cannot revert part"):
            working.revert_to_version(version)

    def test_a_live_reference_is_left_alone(self, models):
        bind_widget(**declared_references())
        working = make_widget(models)
        version = working.create_new_version()
        _widget_model, part_model = models
        other = part_model.objects.create(name="other live")
        working.live_part = other
        working.save()

        working.revert_to_version(version)
        working.refresh_from_db()

        assert working.live_part_id == other.id

    def test_a_private_clone_is_cloned_again_for_the_working_row(self, models):
        bind_widget(part=Reference(Strategy.PRIVATE_CLONE), live_part=Reference(Strategy.LIVE_REFERENCE))
        working = make_widget(models)
        version = working.create_new_version()
        working.part.name = "edited"
        working.part.save()

        working.revert_to_version(version)
        working.refresh_from_db()

        assert working.part_id not in (version.part_id, version.part.working_version_id)
        assert working.part.working_version_id is None
        assert working.part.name == "part"

    def test_the_bridge_carries_an_owned_set_both_ways(self, models):
        calls = []
        bind_widget(
            part=Reference(Strategy.NEW_VERSION, provides="part"),
            live_part=Reference(Strategy.LIVE_REFERENCE),
            children=OwnedSet(via=lambda *args: calls.append(args), accessors=("versions",), reads=("part",)),
        )
        working = make_widget(models)

        version = working.create_new_version()
        working.revert_to_version(version)
        copy = working.create_new_version(is_copy=True)

        assert [(call[0].pk, call[1].pk, call[2]) for call in calls] == [
            (working.pk, version.pk, Operation.PUBLISH),
            (working.pk, version.pk, Operation.REVERT),
            (working.pk, copy.pk, Operation.COPY),
        ]

    def test_a_private_clone_is_cloned_again_on_revert_even_when_owned(self, models):
        """A private target has no family to revert within."""
        bind_widget(**{**declared_references(), "part": Reference(Strategy.PRIVATE_CLONE, owned=True)})
        working = make_widget(models)
        original = working.part_id
        version = working.create_new_version()

        working.revert_to_version(version)
        working.refresh_from_db()

        assert working.part_id not in {original, version.part_id}
        assert working.part.working_version_id is None

    def test_the_working_row_itself_is_not_a_revert_target(self, models):
        bind_widget(**declared_references())
        working = make_widget(models)

        with pytest.raises(ValueError, match="Can only revert to a version of this versioned widget"):
            working.revert_to_version(working)

    def test_revert_keeps_the_counter_another_publisher_advanced(self, models):
        """The save writes every column, so the row is re-read under a lock rather than written from a stale
        instance; the lock comes before anything a member touches."""
        bind_widget(**declared_references())
        working = make_widget(models)
        widget_model, _part_model = models
        version = working.create_new_version()
        stale = widget_model.objects.get(pk=working.pk)
        working.create_new_version()

        with CaptureQueriesContext(connection) as queries:
            stale.revert_to_version(version)

        assert "FOR UPDATE" in statements(queries)[0]
        assert (stale.version_number, widget_model.objects.get(pk=working.pk).version_number) == (3, 3)

    def test_only_a_working_row_reverts_and_only_to_its_own_version(self, models):
        bind_widget(**declared_references())
        working = make_widget(models)
        version = working.create_new_version()
        other = make_widget(models)
        other_version = other.create_new_version()

        with pytest.raises(ValueError, match="Can only revert the working version of this versioned widget"):
            version.revert_to_version(version)
        with pytest.raises(ValueError, match="Can only revert to a version of this versioned widget"):
            working.revert_to_version(other_version)

    def test_a_model_without_a_spec_cannot_revert(self, models):
        _widget_model, part_model = models
        part = part_model.objects.create(name="part")

        with pytest.raises(NotImplementedError, match="VersionedPart has no VersionSpec"):
            part.revert_to_version(part.create_new_version())


class TestKit:
    def test_assert_snapshot_fails_when_an_expected_change_did_not_happen(self, models):
        bind_widget(**declared_references())
        working = make_widget(models)
        version = working.create_new_version()

        with pytest.raises(AssertionError, match="note was expected to change"):
            assert_snapshot(working, version, changed={"note", "part"})

    def test_assert_snapshot_fails_when_an_unexpected_field_differs(self, models):
        bind_widget(**declared_references())
        working = make_widget(models)
        version = working.create_new_version()
        version.note = "drifted"

        with pytest.raises(AssertionError, match="note differs"):
            assert_snapshot(working, version, changed={"part"})

    def test_assert_snapshot_rejects_a_name_that_does_not_travel(self, models):
        bind_widget(**declared_references())
        working = make_widget(models)

        with pytest.raises(AssertionError, match="not travelling fields"):
            assert_snapshot(working, working, changed={"token"})

    def test_assert_family_fails_for_an_unrelated_row(self, models):
        bind_widget(**declared_references())
        working, other = make_widget(models), make_widget(models)

        with pytest.raises(AssertionError):
            assert_family(other, working)


def statements(queries: CaptureQueriesContext) -> list[str]:
    """The SQL a block ran, without the savepoints the test transaction adds."""
    return [query["sql"] for query in queries.captured_queries if "SAVEPOINT" not in query["sql"]]


def part_reference(strategy=Strategy.REUSE_UNCHANGED, **kwargs):
    """A `params["part_id"]` reference mirrored by the `part` column."""
    return ParamReference("part_id", PART, strategy, **kwargs)


def make_param_widget(models, strategy=Strategy.REUSE_UNCHANGED, **kwargs):
    bind_widget(part=part_reference(strategy, **kwargs), live_part=Reference(Strategy.LIVE_REFERENCE))
    _widget_model, part_model = models
    part = part_model.objects.create(name="part")
    return make_widget(models, part=part, params={"part_id": str(part.id)})


class TestParamReference:
    @pytest.mark.parametrize(
        ("strategy", "expectation"),
        [
            pytest.param(Strategy.NEW_VERSION, "new-version", id="new-version"),
            pytest.param(Strategy.REUSE_UNCHANGED, "new-version", id="reuse-unchanged-makes-the-first-version"),
            pytest.param(Strategy.LIVE_REFERENCE, "same-row", id="live-reference"),
            pytest.param(Strategy.PRIVATE_CLONE, "private-clone", id="private-clone"),
        ],
    )
    def test_publish_pins_the_id_in_params_per_strategy(self, models, strategy, expectation):
        working = make_param_widget(models, strategy)
        _widget_model, part_model = models

        version = working.create_new_version()
        pinned = part_model.objects.get(id=version.params["part_id"])

        if expectation == "same-row":
            assert pinned == working.part
        elif expectation == "new-version":
            assert pinned.working_version_id == working.part_id
        else:
            assert pinned.working_version_id is None
            assert pinned.id != working.part_id
        assert working.params["part_id"] == str(working.part_id)

    def test_reuse_unchanged_pins_the_latest_version_until_the_record_changes(self, models):
        working = make_param_widget(models)

        first, second = working.create_new_version(), working.create_new_version()
        working.part.name = "renamed"
        working.part.save()
        third = working.create_new_version()

        assert first.params["part_id"] == second.params["part_id"] != third.params["part_id"]

    def test_an_id_that_names_no_record_or_already_a_version_is_left_alone(self, models):
        working = make_param_widget(models)
        part_version = working.part.create_new_version()
        working.params = {"part_id": str(part_version.id)}
        working.save()

        assert working.create_new_version().params["part_id"] == str(part_version.id)

        working.params = {"part_id": "999999"}
        working.save()

        assert working.create_new_version().params["part_id"] == "999999"

    def test_copy_keeps_params_verbatim(self, models):
        working = make_param_widget(models)

        assert working.create_new_version(is_copy=True).params == working.params

    def test_map_back_reads_the_mirror_column_and_clears_a_dangling_id(self, models):
        working = make_param_widget(models)
        version = working.create_new_version()
        version.part = version.part.__class__.objects.get(id=version.params["part_id"])
        version.save()
        member = part_reference()
        member.name = "part"

        params = dict(version.params)
        member.map_back(version, params)
        assert params["part_id"] == str(working.part_id)

        version.part = None
        params = dict(version.params)
        member.map_back(version, params)
        assert params["part_id"] is None

    def test_revert_maps_the_working_params_back(self, models):
        working = make_param_widget(models)
        version = working.create_new_version()
        version.part_id = int(version.params["part_id"])
        version.save()
        _widget_model, part_model = models
        other = part_model.objects.create(name="other")
        working.params = {"part_id": str(other.id)}
        working.save()

        working.revert_to_version(version)
        working.refresh_from_db()

        assert working.params["part_id"] == str(working.part_id)

    def test_revert_gives_the_working_row_its_own_private_clone(self, models):
        working = make_param_widget(models, Strategy.PRIVATE_CLONE)
        original = working.part_id
        version = working.create_new_version()
        version.part_id = int(version.params["part_id"])
        version.save()

        working.revert_to_version(version)
        working.refresh_from_db()
        _widget_model, part_model = models
        reverted = part_model.objects.get(id=working.params["part_id"])

        assert reverted.id not in {original, version.part_id}
        assert reverted.working_version_id is None

    @pytest.mark.parametrize(
        ("archiving", "archived"),
        [
            pytest.param(Archiving.ARCHIVE, True, id="archive"),
            pytest.param(Archiving.ARCHIVE_VERSIONS_ONLY, False, id="versions-only-spares-a-working-record"),
            pytest.param(Archiving.KEEP, False, id="keep"),
        ],
    )
    def test_archive_referenced_follows_the_policy(self, models, archiving, archived):
        working = make_param_widget(models, Strategy.LIVE_REFERENCE, archiving=archiving)
        member = part_reference(Strategy.LIVE_REFERENCE, archiving=archiving)
        member.name = "part"

        member.archive_referenced(working.params)
        working.part.refresh_from_db()

        assert working.part.is_archived is archived

    def test_a_missing_record_is_logged_and_the_rest_still_archive(self, models, caplog):
        _widget_model, part_model = models
        part = part_model.objects.create(name="part")
        member = part_reference(Strategy.LIVE_REFERENCE, many=True, archiving=Archiving.ARCHIVE)
        member.name = "part"

        with caplog.at_level(logging.ERROR, logger="ocs.versioning"):
            member.archive_referenced({"part_id": ["0", str(part.id)]})
        part.refresh_from_db()

        assert "Cannot archive part_id 0: not found" in caplog.text
        assert part.is_archived

    def test_versions_only_archives_a_version_and_only_archive_restores(self, models):
        working = make_param_widget(models, Strategy.LIVE_REFERENCE)
        part_version = working.part.create_new_version()
        versions_only = part_reference(Strategy.LIVE_REFERENCE, archiving=Archiving.ARCHIVE_VERSIONS_ONLY)
        versions_only.name = "part"
        params = {"part_id": str(part_version.id)}

        versions_only.archive_referenced(params)
        part_version.refresh_from_db()
        assert part_version.is_archived

        versions_only.unarchive_referenced(params)
        part_version.refresh_from_db()
        assert part_version.is_archived

        archive = part_reference(Strategy.LIVE_REFERENCE, archiving=Archiving.ARCHIVE)
        archive.name = "part"
        archive.unarchive_referenced(params)
        part_version.refresh_from_db()
        assert not part_version.is_archived

    def test_a_many_param_is_a_live_reference(self):
        with pytest.raises(VersionSpecError, match="multi-ID"):
            ParamReference("part_ids", PART, Strategy.NEW_VERSION, many=True)

    @pytest.mark.parametrize(
        ("kwargs", "codes"),
        [
            pytest.param({}, [], id="clean"),
            pytest.param({"json_field": "nothing"}, ["E001"], id="json-field-missing"),
            pytest.param({"fk_field": "note"}, ["E001"], id="mirror-not-a-relation"),
            pytest.param({"model_label": "nowhere.Nothing"}, ["E004"], id="model-unresolvable"),
            pytest.param({"model_label": "utils_tests.Collection"}, ["E001"], id="model-not-versioned"),
            pytest.param({"many": True}, ["E001"], id="many-param-on-a-foreign-key"),
            pytest.param({"model_label": WIDGET}, ["E001"], id="mirror-points-at-another-model"),
        ],
    )
    def test_check_names_what_the_model_has(self, models, kwargs, codes):
        widget_model, _part_model = models
        model_label = kwargs.pop("model_label", PART)
        member = ParamReference("part_id", model_label, Strategy.LIVE_REFERENCE, **kwargs)
        member.name = "part"

        assert [code for code, _message in member.check(widget_model)] == codes

    def test_check_rejects_one_id_mirrored_by_a_many_to_many(self):
        member = ParamReference(
            "collection_id", "documents.Collection", Strategy.LIVE_REFERENCE, fk_field="collection_indexes"
        )
        member.name = "collection"

        assert [code for code, _message in member.check(apps.get_model("pipelines", "Node"))] == ["E001"]


class TestMembersFor:
    def test_a_spec_may_narrow_the_members_per_row(self, models):
        def members_for(self, row):
            return tuple(member for member in self.members if member.name != "part")

        bind_widget(**declared_references(), members_for=members_for)
        working = make_widget(models)

        version = working.create_new_version()

        assert version.part_id == working.part_id
        assert working.part.versions.count() == 0


class TestHooks:
    def test_on_clone_runs_before_the_first_save(self, models):
        def on_clone(self, working, clone, *, note="prepared", **options):
            clone.note = note

        spec_cls = bind_widget(**declared_references(), on_clone=on_clone)
        widget_model, _part_model = models
        working = make_widget(models)
        inserted = []

        def record_insert(sender, instance, created, **kwargs):
            if created:
                inserted.append(instance.note)

        post_save.connect(record_insert, sender=widget_model)
        try:
            spec_cls().publish(working, note="set before the insert")
            spec_cls().copy(working)
        finally:
            post_save.disconnect(record_insert, sender=widget_model)

        assert inserted == ["set before the insert", "prepared"]

    def test_on_revert_runs_after_the_working_row_is_saved(self, models):
        seen = {}

        class Observed(VersionSpec):
            class Meta:
                model = WIDGET
                identity = WIDGET_IDENTITY
                regenerated = {"token": uuid.uuid4}

            part = Reference(Strategy.NEW_VERSION)
            live_part = Reference(Strategy.LIVE_REFERENCE)

            def on_revert(self, working, version):
                seen["saved_name"] = type(working).objects.get(pk=working.pk).name

        working = make_widget(models)
        version = working.create_new_version()
        working.name = "edited"
        working.save()

        Observed().revert(working, version)

        assert seen["saved_name"] == "widget"
