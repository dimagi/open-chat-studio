import logging

log = logging.getLogger(__name__)


def update_taskbadger_data(celery_task, message_handler, message):
    try:
        tb_task = celery_task.taskbadger_task
        if tb_task:
            tb_task.update(
                data={
                    "experiment_id": message_handler.experiment.id,
                    "external_chat_id": message_handler.get_chat_id_from_message(message),
                },
                data_merge_strategy="default",
            )
    except Exception:
        log.exception("Error updating taskbadger task")
