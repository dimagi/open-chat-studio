def migrate_step_params(analysis_model):
    from apps.analysis.pipelines import get_data_pipeline, get_source_pipeline

    def _migrate_pipeline_config(pipeline, config):
        for step in pipeline.steps:
            step_id = step.name
            previous_id = step_id.split(":")[0]
            if previous_id in config:
                params = config.pop(previous_id)
                config[step_id] = params
        return config

    for analysis in analysis_model.objects.all():
        source_pipeline = get_source_pipeline(analysis.source)
        data_pipeline = get_data_pipeline(analysis.pipeline)

        config = _migrate_pipeline_config(source_pipeline, analysis.config)
        analysis.config = _migrate_pipeline_config(data_pipeline, config)
        analysis.save()

        for run in analysis.rungroup_set.all():
            params = _migrate_pipeline_config(source_pipeline, run.params)
            run.params = _migrate_pipeline_config(data_pipeline, params)
            run.save()
