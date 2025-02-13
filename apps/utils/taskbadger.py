def update_taskbadger_data(celery_task, message_handler, message):
    tb_task = celery_task.taskbadger_task
    if tb_task:
        tb_task.safe_update(
            data={
                "team": message_handler.experiment.team.slug,
                "experiment_id": message_handler.experiment.id,
                "identifier": message.participant_id,
            },
            data_merge_strategy="default",
        )
