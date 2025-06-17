from celery.app import shared_task
from django.conf import settings
from django.core.mail import send_mail

from apps.pipelines.exceptions import PipelineBuildError, PipelineNodeBuildError
from apps.pipelines.models import Pipeline
from apps.service_providers.llm_service.runnables import GenerationError


@shared_task(ignore_result=True)
def send_email_from_pipeline(recipient_list, subject, message):
    send_mail(
        subject=subject,
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=recipient_list,
        fail_silently=False,
        html_message=message,
    )


@shared_task
def get_response_for_pipeline_test_message(pipeline_id: int, message_text: str, user_id: int):
    """
    Retrieve a response from a pipeline for a test message.
    Attempts to invoke a pipeline with a given message and user, handling potential pipeline build errors.
    """
    pipeline = Pipeline.objects.get(id=pipeline_id)
    errors = pipeline.validate(full=False)
    if errors:
        return {"error": "There are errors in the pipeline configuration. Please correct those before running a test."}
    try:
        return pipeline.simple_invoke(message_text, user_id)
    except PipelineBuildError as e:
        return {"error": e.message}
    except (GenerationError, PipelineNodeBuildError) as e:
        return {"error": str(e)}
