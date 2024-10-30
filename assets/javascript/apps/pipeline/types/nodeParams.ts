import {InputParam} from "./nodeInputTypes";

export type NodeParams = Record<string, string | string[]>;

export type NodeData = {
  label: string;
  value: number;
  type: string;
  inputParams: InputParam[];
  params: NodeParams;
};
