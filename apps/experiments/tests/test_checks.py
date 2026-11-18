"""The versioning system checks: one test per rule, and the whole project's output."""

import pytest
from django.core.checks import Tags, run_checks

from apps.experiments.checks import check_spec, check_version_specs
from apps.experiments.models import ExperimentVersionSpec
from apps.experiments.versioning import Member, OwnedSet, Reference, Strategy, VersionSpec

EXPERIMENT = "experiments.Experiment"
IDENTITY = ExperimentVersionSpec().identity


def declare(*, model=EXPERIMENT, identity=IDENTITY, regenerated=None, **members):
    meta = type("Meta", (), {"model": model, "identity": identity, "regenerated": regenerated or {}})
    return type("ThrowawaySpec", (VersionSpec,), {"Meta": meta, **members})


def bridge(working, other, operation, context):
    return None


def complete_members():
    return {
        "consent_form": Reference(Strategy.REUSE_UNCHANGED),
        "pipeline": Reference(Strategy.NEW_VERSION, owned=True, provides="pipeline"),
    }


def findings(spec_cls):
    return [(message.id, message.msg) for message in check_spec(spec_cls)]


def ids(spec_cls):
    return sorted(code for code, _ in findings(spec_cls))


@pytest.mark.usefixtures("isolated_spec_registry")
class TestSpecChecks:
    def test_a_complete_spec_is_clean(self):
        assert findings(declare(**complete_members())) == []

    @pytest.mark.parametrize(
        ("member_name", "member"),
        [
            pytest.param("seed_message", Reference(Strategy.NEW_VERSION), id="reference-on-a-plain-field"),
            pytest.param("nothing_here", Reference(Strategy.NEW_VERSION), id="reference-on-no-field"),
            pytest.param("children", OwnedSet(via=bridge, accessors=("no_such_set",)), id="owned-set-accessor"),
            pytest.param("nothing_here", Member(), id="plain-member-on-no-field"),
        ],
    )
    def test_E001_a_member_names_something_the_model_has(self, member_name, member):
        spec_cls = declare(**complete_members(), **{member_name: member})

        assert ids(spec_cls) == ["versioning.E001"]

    def test_a_plain_member_may_name_a_field_or_an_accessor(self):
        spec_cls = declare(**complete_members(), seed_message=Member(), static_triggers=Member())

        assert findings(spec_cls) == []

    def test_E002_an_undeclared_reference_to_a_versioned_model(self):
        spec_cls = declare()

        assert [code for code, _ in findings(spec_cls)] == ["versioning.E002", "versioning.E002"]
        assert {message.split(" points at ")[0] for _, message in findings(spec_cls)} == {
            "ThrowawaySpec: experiments.Experiment.pipeline",
            "ThrowawaySpec: experiments.Experiment.consent_form",
        }

    def test_E002_leaves_identity_references_alone(self):
        """The `working_version` self-reference is identity, not content."""
        assert "versioning.E002" not in ids(declare(**complete_members()))

    def test_E003_a_member_reads_before_anyone_provides(self):
        spec_cls = declare(
            consent_form=Reference(Strategy.REUSE_UNCHANGED),
            triggers=OwnedSet(via=bridge, accessors=("static_triggers",), reads=("pipeline",)),
            pipeline=Reference(Strategy.NEW_VERSION, owned=True, provides="pipeline"),
        )

        assert ids(spec_cls) == ["versioning.E003"]

    @pytest.mark.parametrize(
        "meta",
        [
            pytest.param({"identity": IDENTITY | {"colour"}}, id="identity"),
            pytest.param({"regenerated": {"colour": lambda: 1}}, id="regenerated"),
        ],
    )
    def test_E004_identity_and_regenerated_name_real_fields(self, meta):
        assert ids(declare(**complete_members(), **meta)) == ["versioning.E004"]

    def test_E004_the_model_label_resolves(self):
        assert ids(declare(model="nowhere.Nothing")) == ["versioning.E004"]

    @pytest.mark.parametrize(
        "spec_kwargs",
        [
            pytest.param({"model": "nowhere.Nothing"}, id="unresolvable-model"),
            pytest.param({"nothing_here": Reference(Strategy.PRIVATE_CLONE)}, id="reference-on-no-field"),
        ],
    )
    def test_a_private_clone_on_a_broken_declaration_does_not_abort_the_run(self, spec_kwargs):
        """The model check collects every finding; a declaration E001 or E004 already reported is skipped there."""
        members = {"action": Reference(Strategy.PRIVATE_CLONE)} if "model" in spec_kwargs else {}
        declare(**complete_members(), **members, **spec_kwargs)

        reported = sorted(message.id for message in check_version_specs())

        assert "versioning.W001" in reported
        assert reported[0] in ("versioning.E001", "versioning.E004")


def test_the_bound_experiment_spec_is_clean():
    assert findings(ExperimentVersionSpec) == []


def test_the_project_reports_only_the_known_anomalies():
    """Two versioned models lack the family columns; nothing else is reported, so any new finding fails here."""
    messages = run_checks(tags=[Tags.models])

    reported = sorted(
        (message.id, message.obj._meta.label) for message in messages if message.id.startswith("versioning.")
    )
    assert reported == [
        ("versioning.W001", "custom_actions.CustomActionOperation"),
        ("versioning.W001", "events.EventAction"),
    ]
