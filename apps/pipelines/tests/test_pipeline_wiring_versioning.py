"""How a pipeline's wiring reaches its version comparison.

Edges live in ``Pipeline.data`` rather than in a row of their own (ADR-0049), so the comparison has
to read them from there -- otherwise a version that only rewires the graph reports no changes.
"""

import pytest

from apps.pipelines.const import STANDARD_INPUT_NAME, STANDARD_OUTPUT_NAME
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.pipelines import NodeFactory, PipelineFactory


@pytest.mark.django_db()
class TestPipelineWiringVersionDetails:
    def _rewire(self, pipeline, edges):
        pipeline.data = {**pipeline.data, "edges": edges}
        pipeline.save(update_fields=["data"])

    def test_unwiring_an_edge_is_a_change(self):
        pipeline = PipelineFactory.create()
        pipeline.create_new_version()

        self._rewire(pipeline, [])

        assert pipeline.compare_with_latest() is True

    def test_wiring_an_edge_up_is_a_change(self):
        pipeline = PipelineFactory.create()
        self._rewire(pipeline, [])
        pipeline.create_new_version()

        self._rewire(pipeline, [{"id": "1->2", "source": "start", "target": "end"}])

        assert pipeline.compare_with_latest() is True

    def test_repointing_an_edge_is_a_change(self):
        """The wire's endpoints changed while its id did not -- an id-only comparison would miss it."""
        pipeline = PipelineFactory.create()
        NodeFactory.create(flow_id="middle", pipeline=pipeline)
        pipeline.create_new_version()

        self._rewire(pipeline, [{"id": "1->2", "source": "start", "target": "middle"}])

        assert pipeline.compare_with_latest() is True

    def test_a_new_id_for_the_same_wire_is_not_a_change(self):
        """Edge ids are client-generated, so deleting a wire and drawing the same one again would
        otherwise read as a change to a graph that is wired identically."""
        pipeline = PipelineFactory.create()
        pipeline.create_new_version()

        self._rewire(pipeline, [{"id": "drawn-again", "source": "start", "target": "end"}])

        assert pipeline.compare_with_latest() is False

    def test_an_absent_handle_matches_the_standard_one_it_stands_for(self):
        """The builder names its source handles and a seeded graph does not, so the two spellings of
        the standard handle have to compare as one wire."""
        pipeline = PipelineFactory.create()
        self._rewire(
            pipeline,
            [
                {
                    "id": "1->2",
                    "source": "start",
                    "target": "end",
                    "sourceHandle": STANDARD_OUTPUT_NAME,
                    "targetHandle": STANDARD_INPUT_NAME,
                }
            ],
        )
        pipeline.create_new_version()

        self._rewire(
            pipeline, [{"id": "1->2", "source": "start", "target": "end", "sourceHandle": None, "targetHandle": None}]
        )

        assert pipeline.compare_with_latest() is False

    def test_the_order_edges_are_stored_in_is_not_a_change(self):
        pipeline = PipelineFactory.create()
        NodeFactory.create(flow_id="middle", pipeline=pipeline)
        edges = [
            {"id": "a", "source": "start", "target": "middle"},
            {"id": "b", "source": "middle", "target": "end"},
        ]
        self._rewire(pipeline, edges)
        pipeline.create_new_version()

        self._rewire(pipeline, list(reversed(edges)))

        assert pipeline.compare_with_latest() is False

    def test_a_rewiring_reaches_the_experiment_comparison(self):
        """The comparison the web app's "unreleased changes" badge and the write API's
        nothing-to-publish refusal are both drawn from."""
        pipeline = PipelineFactory.create()
        experiment = ExperimentFactory.create(team=pipeline.team, pipeline=pipeline)
        experiment.create_new_version()

        self._rewire(pipeline, [])
        experiment.refresh_from_db()

        assert experiment.compare_with_latest() is True
