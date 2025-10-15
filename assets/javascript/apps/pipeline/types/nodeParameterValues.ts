export class Option {
      value: string;
      label: string;
      edit_url?: string;
      /**
       * Optional conditional values for this option.
       * If provided, the option will only be displayed for the specified conditionals.
       */
      conditionalValues?: string[] | undefined;

      constructor(
        value: string,
        label: string,
        edit_url?: string,
        conditionalValues?: string[] | undefined,
      ) {
        this.value = value;
        this.label = label;
        this.edit_url = edit_url;
        this.conditionalValues = conditionalValues;
      }

      /**
       * Checks if the option should be displayed for a given conditional.
       * If no conditionalValue is set, the option is displayed for all conditionals.
       *
       * @param {string} conditional - The conditional to check against.
       * @returns {boolean} - True if the option should be displayed, false otherwise.
       */
      displayForConditional(conditional: string): boolean {
        return !this.conditionalValues || this.conditionalValues.includes(conditional);
      }
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
