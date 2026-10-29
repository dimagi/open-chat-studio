"""The LLM Router's prompt modes and the standard prompt template (#2688)."""

import json
from pathlib import Path
from string import Formatter
from typing import Literal
from unittest import mock

import pytest
from pydantic import Field, create_model
from pydantic import ValidationError as PydanticValidationError

from apps.pipelines.nodes.base import PipelineState
from apps.pipelines.nodes.context import NodeContext
from apps.pipelines.nodes.nodes import RouterNode
from apps.pipelines.nodes.router_prompts import (
    LATEST_ROUTER_PROMPT_VERSION,
    ROUTER_PROMPT_TEMPLATES,
    render_router_prompt,
)
from apps.pipelines.repository import ORMRepository
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.pipelines import NodeFactory, PipelineFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory
from apps.utils.tests.langchain import build_fake_llm_echo_service

# Shared with `renderRouterPromptPreview`'s tests in assets/javascript/apps/pipeline/utils.test.tsx,
# so the builder's preview fails CI when it stops matching the prompt the router sends.
PREVIEW_CASES = json.loads((Path(__file__).parent / "data" / "router_prompt_preview_cases.json").read_text())


def _router(**params) -> RouterNode:
    return RouterNode(
        node_id="router",
        django_node=None,
        name="router",
        keywords=["billing", "support"],
        route_descriptions={"billing": "Payments and invoices"},
        llm_provider_id=LlmProviderFactory.create().id,
        llm_provider_model_id=LlmProviderModelFactory.create().id,
        **params,
    )


@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
@mock.patch("apps.pipelines.nodes.nodes.create_agent")
def _system_prompt(router: RouterNode, create_agent_mock, get_llm_service) -> str:
    """The system prompt the router sends to the model for one message."""
    get_llm_service.return_value = build_fake_llm_echo_service()
    RouterOutput = create_model("RouterOutput", route=(Literal["BILLING"], Field(description="Selected route")))
    create_agent_mock.return_value.invoke.return_value = {"structured_response": RouterOutput(route="BILLING")}
    session = ExperimentSessionFactory.create()
    router._repo = ORMRepository(session=session)
    router._process_conditional(
        NodeContext(PipelineState(messages=["hi"], experiment_session=session, last_node_input="hi"))
    )
    return create_agent_mock.call_args[1]["system_prompt"].content


class TestRenderRouterPrompt:
    def test_fills_the_context_and_default_route_and_keeps_the_routes_variable(self):
        template = render_router_prompt(LATEST_ROUTER_PROMPT_VERSION, "Refunds are billing.", "BILLING")

        assert "Available routes:\n{routes}" in template
        assert "Refunds are billing." in template
        assert "choose BILLING." in template

    def test_leaves_out_an_empty_context(self):
        template = render_router_prompt(LATEST_ROUTER_PROMPT_VERSION, "  ", "BILLING")

        assert "Additional context" not in template
        assert "\n\n\n" not in template

    def test_escapes_braces_in_the_default_route(self):
        template = render_router_prompt(LATEST_ROUTER_PROMPT_VERSION, "", "{ODD}")

        assert "choose {ODD}." in template.format(routes="- {ODD}")

    def test_a_placeholder_in_the_default_route_is_not_filled(self):
        template = render_router_prompt(LATEST_ROUTER_PROMPT_VERSION, "Refunds are billing.", "{custom_context}")

        assert "choose {custom_context}." in template.format(routes="")
        assert template.count("Refunds are billing.") == 1


@pytest.mark.django_db()
class TestSystemPrompt:
    def test_standard_builds_the_prompt_from_the_routes(self):
        prompt = _system_prompt(_router(prompt_mode="standard", prompt="Ignored prompt", custom_context="Ignored"))

        assert prompt.startswith("You are a routing assistant.")
        assert "- BILLING: Payments and invoices\n- SUPPORT" in prompt
        assert "choose BILLING." in prompt
        assert "Ignored" not in prompt

    def test_standard_with_context_adds_the_context(self):
        router = _router(prompt_mode="standard_with_context", custom_context="Refunds count as {routes_list}.")

        prompt = _system_prompt(router)

        assert prompt.startswith("You are a routing assistant.")
        assert "Refunds count as BILLING, SUPPORT." in prompt

    @pytest.mark.parametrize(
        "params",
        [
            pytest.param({"prompt_mode": "custom"}, id="custom"),
            pytest.param({}, id="no-mode-is-custom"),
        ],
    )
    def test_custom_sends_the_users_prompt(self, params):
        prompt = _system_prompt(_router(prompt="Pick a route.", **params))

        assert prompt == "Pick a route.\nThe default routing destination is: BILLING"

    def test_a_pinned_template_version_is_used(self):
        templates = {"v0": "Old template.\n{routes}\n{custom_context}\nDefault: {default_route}"}
        with mock.patch.dict("apps.pipelines.nodes.router_prompts.ROUTER_PROMPT_TEMPLATES", templates):
            prompt = _system_prompt(_router(prompt_mode="standard", prompt_template_version="v0"))

        assert prompt.startswith("Old template.")


@pytest.mark.django_db()
class TestValidation:
    def test_an_unknown_template_version_is_rejected(self):
        with pytest.raises(PydanticValidationError, match="Unknown router prompt template version: v99"):
            _router(prompt_mode="standard", prompt_template_version="v99")

    @pytest.mark.parametrize(
        ("params", "is_valid"),
        [
            pytest.param({"prompt_mode": "custom", "prompt": "{unknown}"}, False, id="custom-checks-prompt"),
            pytest.param({"prompt_mode": "standard", "prompt": "{unknown}"}, True, id="standard-ignores-prompt"),
            pytest.param(
                {"prompt_mode": "standard_with_context", "custom_context": "{unknown}"},
                False,
                id="context-mode-checks-context",
            ),
            pytest.param(
                {"prompt_mode": "standard", "custom_context": "{unknown}"}, True, id="standard-ignores-context"
            ),
        ],
    )
    def test_only_the_text_the_mode_uses_is_validated(self, params, is_valid):
        if is_valid:
            _router(**params)
        else:
            with pytest.raises(PydanticValidationError, match="unknown variables: unknown"):
                _router(**params)

    @pytest.mark.parametrize(
        ("prompt_mode", "is_valid"),
        [
            pytest.param("custom", False, id="custom-requires-prompt"),
            pytest.param("standard", True, id="standard-allows-empty-prompt"),
            pytest.param("standard_with_context", True, id="context-mode-allows-empty-prompt"),
        ],
    )
    def test_an_empty_prompt_is_rejected_only_in_custom_mode(self, prompt_mode, is_valid):
        if is_valid:
            _router(prompt_mode=prompt_mode, prompt="")
        else:
            with pytest.raises(PydanticValidationError, match="Prompt cannot be empty"):
                _router(prompt_mode=prompt_mode, prompt="")


@pytest.mark.django_db()
class TestTemplateVersion:
    @pytest.mark.parametrize(
        ("params", "expected_version"),
        [
            pytest.param({}, LATEST_ROUTER_PROMPT_VERSION, id="new-router-uses-latest"),
            pytest.param({"prompt_template_version": None}, LATEST_ROUTER_PROMPT_VERSION, id="null-uses-latest"),
            pytest.param({"prompt_template_version": "v0"}, "v0", id="pinned-version-kept"),
        ],
    )
    def test_a_router_is_pinned_to_a_template_version(self, params, expected_version):
        with mock.patch.dict("apps.pipelines.nodes.router_prompts.ROUTER_PROMPT_TEMPLATES", {"v0": "{routes}"}):
            router = _router(prompt_mode="standard", **params)

        assert router.prompt_template_version == expected_version


@pytest.mark.django_db()
class TestPipelineVersions:
    TEMPLATES = {"v1": "Old {routes}", "v2": "New {routes}"}

    @pytest.fixture()
    def router_node(self):
        params = {"name": "r", "prompt_mode": "standard", "prompt_template_version": "v1"}
        return NodeFactory.create(type="RouterNode", pipeline=PipelineFactory.create(), params=params)

    def test_a_version_keeps_the_working_nodes_template_after_a_newer_one_is_released(self, router_node):
        with (
            mock.patch.dict("apps.pipelines.nodes.router_prompts.ROUTER_PROMPT_TEMPLATES", self.TEMPLATES),
            mock.patch("apps.pipelines.nodes.router_prompts.LATEST_ROUTER_PROMPT_VERSION", "v2"),
        ):
            version = router_node.pipeline.create_new_version()

        assert version.node_set.get(type="RouterNode").params["prompt_template_version"] == "v1"

    def test_the_working_node_reports_no_change_against_its_version(self, router_node):
        router_node.pipeline.create_new_version()

        router_node.refresh_from_db()
        assert not router_node.compare_with_latest()

    def test_switching_template_version_is_an_unpublished_change(self, router_node):
        router_node.pipeline.create_new_version()

        router_node.refresh_from_db()
        router_node.params["prompt_template_version"] = "v2"
        assert router_node.compare_with_latest()

    def test_reverting_to_a_version_restores_its_template_version(self, router_node):
        pipeline = router_node.pipeline
        version = pipeline.create_new_version()
        router_node.params["prompt_template_version"] = "v2"
        router_node.save()

        pipeline.revert_to_version(version)

        assert pipeline.node_set.get(type="RouterNode").params["prompt_template_version"] == "v1"


@pytest.mark.django_db()
class TestPreviewContract:
    @pytest.mark.parametrize("case", [pytest.param(case, id=case["id"]) for case in PREVIEW_CASES["cases"]])
    def test_the_router_sends_the_prompt_the_builder_previews(self, case):
        templates = {"contract": PREVIEW_CASES["template"]}
        with mock.patch.dict("apps.pipelines.nodes.router_prompts.ROUTER_PROMPT_TEMPLATES", templates):
            router = RouterNode(
                node_id="router",
                django_node=None,
                name="router",
                llm_provider_id=LlmProviderFactory.create().id,
                llm_provider_model_id=LlmProviderModelFactory.create().id,
                prompt_template_version="contract",
                **{name: case[name] for name in ("keywords", "route_descriptions", "default_keyword_index")},
                **{name: case[name] for name in ("prompt_mode", "custom_context")},
            )
            prompt = _system_prompt(router)

        assert prompt == case["expected"]

    @pytest.mark.parametrize("version", ROUTER_PROMPT_TEMPLATES)
    def test_every_template_uses_only_placeholders_the_preview_fills(self, version):
        # The preview fills plain `{name}` fields only, so a template may not use format specs or conversions.
        assert _fields(ROUTER_PROMPT_TEMPLATES[version]) <= _fields(PREVIEW_CASES["template"])

    @pytest.mark.parametrize(
        "custom_context",
        [
            pytest.param("{routes:>10}", id="format-spec"),
            pytest.param("{routes!r}", id="conversion"),
            pytest.param("{0}", id="positional"),
            pytest.param("{}", id="auto-numbered"),
            pytest.param("{routes[0]}", id="index"),
            pytest.param("{routes_list.upper}", id="attribute"),
        ],
    )
    def test_context_cannot_use_format_syntax_the_preview_does_not_fill(self, custom_context):
        """The preview fills `{routes}` and `{routes_list}` only as plain fields, and leaves run-time data
        such as `{participant_data.plan}` as written."""
        with pytest.raises(PydanticValidationError):
            _router(prompt_mode="standard_with_context", custom_context=custom_context)


def _fields(template: str) -> set[tuple[str, str | None, str | None]]:
    return {(name, spec, conversion) for _, name, spec, conversion in Formatter().parse(template) if name is not None}
