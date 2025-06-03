export class Option {
      value: string;
      label: string;
      edit_url?: string;
      /**
       * Optional discriminator values for this option.
       * If provided, the option will only be displayed for the specified discriminators.
       */
      discriminatorValue?: string[] | undefined;

      constructor(
        value: string,
        label: string,
        edit_url?: string,
        discriminatorValue?: string[] | undefined,
      ) {
        this.value = value;
        this.label = label;
        this.edit_url = edit_url;
        this.discriminatorValue = discriminatorValue;
      }

      /**
       * Checks if the option should be displayed for a given discriminator.
       * If no discriminatorValue is set, the option is displayed for all discriminators.
       *
       * @param {string} discriminator - The discriminator to check against.
       * @returns {boolean} - True if the option should be displayed, false otherwise.
       */
      displayForDiscriminator(discriminator: string): boolean {
        return !this.discriminatorValue || this.discriminatorValue.includes(discriminator);
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
