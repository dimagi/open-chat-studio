import json
from functools import cached_property

import pandas as pd

from apps.analysis.core import BaseStep, Params, PipeOut, StepContext, required
from apps.analysis.models import Resource, ResourceType


class BaseLoader(BaseStep[None, PipeOut]):
    """Base class for steps that load data from a resource."""

    input_type = None

    def run(self, params: Params, data: None = None) -> tuple[str, dict]:
        return self.load(params)

    def load(self, params: Params) -> tuple[str, dict]:
        raise NotImplementedError()


class ResourceLoaderParams(Params):
    resource_id: required(int) = None

    def get_dynamic_config_form_class(self):
        from .forms import ResourceLoaderParamsForm

        return ResourceLoaderParamsForm

    @cached_property
    def resource(self):
        return Resource.objects.get(id=self.resource_id)


class ResourceTextLoader(BaseLoader[str]):
    param_schema = ResourceLoaderParams
    output_type = str

    def load(self, params: ResourceLoaderParams) -> StepContext[str]:
        with params.resource.file.open("r") as file:
            data = file.read()
            lines = len(data.splitlines())
            self.log.info(f"Loaded {lines} lines of text")
            return StepContext(data, name="text")


class ResourceDataframeLoader(BaseLoader[pd.DataFrame]):
    param_schema = ResourceLoaderParams
    output_type = pd.DataFrame

    def load(self, params: ResourceLoaderParams) -> StepContext[pd.DataFrame]:
        parser = self._get_parser(params.resource.type)
        with params.resource.file.open("r") as file:
            data = parser(file)
            self.log.info(f"Loaded {len(data)} rows")
            return StepContext(data, name="dataframe")

    def _get_parser(self, type_: ResourceType):
        if type_ == ResourceType.CSV:
            return pd.read_csv
        elif type_ == ResourceType.JSON:

            def _json_parser(file):
                return pd.json_normalize(json.load(file))

            return _json_parser
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
