## Anatomy of a node

```python
from apps.pipelines.nodes.base import *
from pydantic import Field

class FancyNode(PipelineNode):
    """The class docstring is the node help text which is shown in the UI"""

    # All nodes must define a label
    model_config = ConfigDict(json_schema_extra=NodeSchema(label="Fancy"))
    
    # required field    
    param1: int
   
    # required field with help text and validation
    param2: int = Field(..., description="This is the help text", gt=0, lt=10)
    
    # not required because they have a default value
    param3: str = ""
    param4: int | None = None
    
    # field with custom widget
    param5: str = Field("", json_schema_extra=UiSchema(widget=Widgets.expandable_text))
    
    # field with choices displayed as a select widget
    param6: MyTextChoices = Field(..., json_schema_extra=UiSchema(
        widget=Widgets.select, enum_labels=MyTextChoices.labels()
    ))
    
    # field with choices loaded from an external source
    param7: int = Field(..., json_schema_extra=UiSchema(
        widget=Widgets.select, options_source=OptionsSource.source_material
    ))
    
    def _process(self, input, state: PipelineState, node_id: str) -> str:
        self.logger.debug(f"Returning input: '{input}' without modification", input=input, output=input)
        return input
```
