from django.urls import path


def make_crud_urls(views_module, model_name: str, prefix: str = ""):
    f"""Make the CRUD URLs for a given model.

    Views (all are expected to be class based views):
    * home: {model_name}Home - name: {prefix}_home
    * new: Create{model_name} - name: {prefix}_new
    * edit: Edit{model_name} - name: {prefix}_edit
    * delete: Delete{model_name} - name: {prefix}_delete
    * table: {model_name}TableView - name: {prefix}_table

    Args:
        views_module (module): The module containing the views.
        model_name (str): The class name of the model. This is used to generate the view class names.
        prefix (str, optional): The prefix for the URLs. Defaults to None.
    """
    url_prefix = f"{prefix}/" if prefix else ""
    name_prefix = f"{prefix}_" if prefix else ""
    return [
        path(f"{url_prefix}", getattr(views_module, f"{model_name}Home").as_view(), name=f"{name_prefix}home"),
        path(f"{url_prefix}new/", getattr(views_module, f"Create{model_name}").as_view(), name=f"{name_prefix}new"),
        path(f"{url_prefix}<int:pk>/", getattr(views_module, f"Edit{model_name}").as_view(), name=f"{name_prefix}edit"),
        path(
            f"{url_prefix}<int:pk>/delete/",
            getattr(views_module, f"Delete{model_name}").as_view(),
            name=f"{name_prefix}delete",
        ),
        path(
            f"{url_prefix}table/", getattr(views_module, f"{model_name}TableView").as_view(), name=f"{name_prefix}table"
        ),
    ]
