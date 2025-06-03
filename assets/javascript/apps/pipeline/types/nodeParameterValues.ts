    export type Option = {
      value: string;
      label: string;
      edit_url?: string | undefined;
      discriminatorValue?: string | undefined;
    }

    export type TypedOption = {
      value: string;
      label: string;
      type: string;
    }


    export type NodeParameterValues = {
      LlmProviderId: TypedOption[];
      LlmProviderModelId: TypedOption[];
      source_material: Option[];
      assistant: Option[];
      collection: Option[];
      collection_index: Option[];
      agent_tools: Option[];
      custom_actions: Option[];
      [k: string]: any;
    };

   export type LlmProviderModel = TypedOption & {
    max_token_limit: number
   };
