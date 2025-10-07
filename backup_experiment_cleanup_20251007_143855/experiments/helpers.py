from field_audit.models import AuditAction

from apps.experiments.models import Experiment


def get_real_user_or_none(user):
    if user.is_anonymous:
        return None
    else:
        return user


def update_experiment_name_by_pipeline_id(pipeline_id, new_name):
    try:
        experiments_to_update = Experiment.objects.filter(pipeline_id=pipeline_id)
        experiments_to_update.update(name=new_name, audit_action=AuditAction.AUDIT)
        return True
    except Experiment.DoesNotExist:
        return False
