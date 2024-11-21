export type SimplePipelineMessageResponse = {
  task_id: string;
};


type TaskResult = {
  messages: string[];
  outputs: Outputs;
} | string;

type Outputs = {
  [key: string]: {
    message: string;
    output_handle?: string;
  };
};

export type TestMessageTaskResponse = {
  state: string;
  complete: boolean;
  success: boolean | null;
  progress: Record<string, string>;
  result?: TaskResult;
};
