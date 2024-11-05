import {ValidatorSpec} from "../nodes/InputValidators";


export type InputParam = {
  name: string;
  human_name?: string;
  type: string;
  default?: unknown;
  help_text: string;
  validators: ValidatorSpec[]
};

export type NodeInputTypes = {
  name: string;
  human_name: string;
  input_params: InputParam[];
  node_description: string;
};
