from django.db.models import F, QuerySet


class CustomActionOperationMixin:
    def _copy_custom_action_operations_to_new_version(self, new_assistant=None, new_node=None, is_copy: bool = False):
        for operation in self.get_custom_action_operations():
            operation.create_new_version(
                new_assistant=new_assistant,
                new_node=new_node,
                is_copy=is_copy,
            )

    def get_custom_action_operations(self) -> QuerySet:
        if self.is_working_version:
            # only include operations that are still enabled by the action
            return self.custom_action_operations.select_related("custom_action").filter(
                custom_action__allowed_operations__contains=[F("operation_id")]
            )
        else:
            return self.custom_action_operations.select_related("custom_action")
