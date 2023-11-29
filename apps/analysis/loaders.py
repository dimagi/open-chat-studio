import json
from functools import cached_property

import pandas as pd

from . import forms
from .models import Resource, ResourceType
from .steps import BaseStep, Params, V, required


class BaseLoader(BaseStep[None, V]):
    input_type = None

    def run(self, params: Params, data: None = None) -> tuple[str, dict]:
        return self.load(params)

    def load(self, params: Params) -> tuple[str, dict]:
        raise NotImplementedError()


class ResourceLoaderParams(Params):
    resource_id: required(int) = None

    def get_form_class(self):
        return forms.ResourceLoaderParamsForm

    @cached_property
    def resource(self):
        return Resource.objects.get(id=self.resource_id)


class ResourceTextLoader(BaseLoader[str]):
    output_type = str

    def load(self, params: ResourceLoaderParams) -> tuple[str, dict]:
        with params.resource.file.open("r") as file:
            return file.read().decode(), {}


class ResourceDataframeLoader(BaseLoader[pd.DataFrame]):
    param_schema = ResourceLoaderParams
    output_type = pd.DataFrame

    def load(self, params: ResourceLoaderParams) -> tuple[pd.DataFrame, dict]:
        parser = self._get_parser(params.resource.type)
        with params.resource.file.open("r") as file:
            data = parser(file)
            return data, {}

    def _get_parser(self, type_: ResourceType):
        if type_ == ResourceType.CSV:
            return pd.read_csv
        elif type_ == ResourceType.JSONL:

            def _jsonl_parser(file):
                lines = file.read().splitlines()
                df_inter = pd.DataFrame(lines)
                df_inter.columns = ["json_element"]
                return pd.json_normalize(df_inter["json_element"].apply(json.loads))

            return _jsonl_parser

        elif type_ == ResourceType.TEXT:

            def _text_parser(file):
                lines = file.read().splitlines()
                df = pd.DataFrame(lines)
                df.columns = ["line"]
                return df

            return _text_parser

        elif type_ == ResourceType.XLSX:
            return pd.read_excel
        else:
            raise ValueError(f"Unsupported resource type: {type_}")
