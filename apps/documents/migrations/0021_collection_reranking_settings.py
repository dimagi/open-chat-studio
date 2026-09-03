import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0020_collection_hybrid_search_settings"),
        # The migration that creates LlmProvider is all the reranker_provider FK needs in state.
        # makemigrations pins the current service_providers leaf instead, plus the current leaf of
        # files and teams, which nothing here touches. The service_providers leaf is not merely
        # redundant: depending on it pulls it ahead of pipelines.0029 in the graph, and its
        # notify_deprecated_models data migration reads pipelines_node through the live model, so
        # a fresh migrate then fails on the column 0029 has not added yet.
        ("service_providers", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="collection",
            name="enable_reranking",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "If enabled, retrieval candidates are rescored against the query by a reranker "
                    "before the best of them are returned."
                ),
            ),
        ),
        migrations.AddField(
            model_name="collection",
            name="reranker_provider",
            field=models.ForeignKey(
                blank=True,
                null=True,
                help_text=(
                    "Provider whose credentials are used for reranking. Only Voyage AI offers a rerank endpoint."
                ),
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="service_providers.llmprovider",
            ),
        ),
        migrations.AddField(
            model_name="collection",
            name="rerank_model",
            field=models.CharField(
                default="rerank-2",
                help_text=(
                    "Reranker model, named as the provider names it. A model the provider does not "
                    "recognise leaves retrieval on its un-reranked ranking."
                ),
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="collection",
            name="rerank_top_n",
            field=models.PositiveIntegerField(
                default=50,
                help_text=(
                    "How many retrieval candidates to rescore. This is what bounds the per-query cost "
                    "of reranking, since the reranker is charged per candidate."
                ),
                validators=[django.core.validators.MinValueValidator(1)],
            ),
        ),
        migrations.AddConstraint(
            model_name="collection",
            constraint=models.CheckConstraint(
                condition=models.Q(("rerank_top_n__gte", 1)),
                name="collection_rerank_top_n_at_least_1",
            ),
        ),
    ]
