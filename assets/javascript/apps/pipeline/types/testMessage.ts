export type SimplePipelineMessageResponse = {
  task_id: string;
};


type TaskResult = {
  error?: string;
  interrupt?: {message: string; tag_name: string};
  messages: string[];
  outputs: Outputs;
} | string;

type Outputs = {
  [key: string]: {
    node_id: string;
    message: string;
    output_handle?: string;
    route?: string;
  };
};

export type TestMessageTaskResponse = {
  state: string;
  complete: boolean;
  success: boolean | null;
  progress: Record<string, string>;
  result?: TaskResult;
};
