import { getCSRFToken } from '../utils/cookies';

export type ChatRole = 'system' | 'user' | 'assistant';

export interface ChatAttachment {
  name: string;
  content_type: string;
  size: number;
}

export interface ChatMessage {
  created_at: string;
  role: ChatRole;
  content: string;
  metadata?: unknown;
  attachments?: ChatAttachment[];
}

export interface ChatStartSessionResponse {
  session_id: string;
  chatbot: unknown;
  participant: unknown;
}

export interface ChatSendMessageResponse {
  task_id: string;
  status: 'processing' | 'completed' | 'error';
  error?: string;
}

export interface ChatTaskPollResponse {
  message?: ChatMessage;
  status: 'processing' | 'complete';
  error?: string;
}

export interface ChatPollResponse {
  messages: ChatMessage[];
  has_more: boolean;
  session_status: 'active' | 'ended';
}

export interface ChatSessionServiceOptions {
  apiBaseUrl: string;
  embedKey?: string;
  widgetVersion: string;
  csrfTokenProvider?: (apiBaseUrl: string) => string | undefined;
  taskPollingIntervalMs?: number;
  taskPollingMaxAttempts?: number;
  messagePollingIntervalMs?: number;
}

export interface TaskPollingCallbacks {
  onMessage: (message: ChatMessage) => void;
  onProgress?: (message: string) => void;
  onTimeout?: () => void;
  onError?: (error: Error) => void;
}

export interface TaskPollingHandle {
  cancel: () => void;
}

export interface MessagePollingCallbacks {
  getSince: () => string | undefined;
  onMessages: (messages: ChatMessage[]) => void;
  onError?: (error: Error) => void;
}

export interface MessagePollingHandle {
  stop: () => void;
}

export class ChatSessionService {
  private readonly apiBaseUrl: string;
  private readonly embedKey?: string;
  private readonly widgetVersion: string;
  private readonly csrfTokenProvider: (apiBaseUrl: string) => string | undefined;
  private readonly taskPollingIntervalMs: number;
  private readonly taskPollingMaxAttempts: number;
  private readonly messagePollingIntervalMs: number;
  private messagePollingTimer?: ReturnType<typeof setInterval>;

  constructor(options: ChatSessionServiceOptions) {
    this.apiBaseUrl = options.apiBaseUrl;
    this.embedKey = options.embedKey;
    this.widgetVersion = options.widgetVersion;
    this.csrfTokenProvider = options.csrfTokenProvider ?? getCSRFToken;
    this.taskPollingIntervalMs = options.taskPollingIntervalMs ?? 1000;
    this.taskPollingMaxAttempts = options.taskPollingMaxAttempts ?? 120;
    this.messagePollingIntervalMs = options.messagePollingIntervalMs ?? 30000;
  }

  async startSession(requestBody: Record<string, unknown>): Promise<ChatStartSessionResponse> {
    const response = await fetch(`${this.apiBaseUrl}/api/chat/start/`, {
      method: 'POST',
      headers: this.getJsonHeaders(),
      body: JSON.stringify(requestBody),
    });

    if (!response.ok) {
      throw new Error(`Failed to start session: ${response.statusText}`);
    }

    return response.json() as Promise<ChatStartSessionResponse>;
  }

  async sendMessage(sessionId: string, payload: Record<string, unknown>): Promise<ChatSendMessageResponse> {
    const response = await fetch(`${this.apiBaseUrl}/api/chat/${sessionId}/message/`, {
      method: 'POST',
      headers: this.getJsonHeaders(),
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      throw new Error(`Failed to send message: ${response.statusText}`);
    }

    return response.json() as Promise<ChatSendMessageResponse>;
  }

  async pollTaskOnce(sessionId: string, taskId: string): Promise<ChatTaskPollResponse> {
    const response = await fetch(`${this.apiBaseUrl}/api/chat/${sessionId}/${taskId}/poll/`, {
      headers: this.getCommonHeaders(),
    });

    if (!response.ok) {
      throw new Error(`Failed to poll task: ${response.statusText}`);
    }

    return response.json() as Promise<ChatTaskPollResponse>;
  }

  pollTask(sessionId: string, taskId: string, callbacks: TaskPollingCallbacks): TaskPollingHandle {
    let attempts = 0;
    let cancelled = false;
    let timeoutId: ReturnType<typeof setTimeout> | undefined;

    const scheduleNextPoll = () => {
      timeoutId = setTimeout(() => {
        void poll();
      }, this.taskPollingIntervalMs);
    };

    const poll = async () => {
      if (cancelled) {
        return;
      }

      try {
        const data = await this.pollTaskOnce(sessionId, taskId);

        if (data.error) {
          throw new Error(data.error);
        }

        if (data.status === 'complete' && data.message) {
          callbacks.onMessage(data.message);
          return;
        }

        if (data.status === 'processing' && data.message?.content && callbacks.onProgress) {
          callbacks.onProgress(data.message.content);
        }

        attempts += 1;
        if (attempts >= this.taskPollingMaxAttempts) {
          if (callbacks.onTimeout) {
            callbacks.onTimeout();
          }
          return;
        }

        scheduleNextPoll();
      } catch (error) {
        if (callbacks.onError) {
          callbacks.onError(error instanceof Error ? error : new Error('Failed to get response'));
        }
      }
    };

    void poll();

    return {
      cancel: () => {
        cancelled = true;
        if (timeoutId) {
          clearTimeout(timeoutId);
        }
      },
    };
  }

  async fetchMessages(sessionId: string, since?: string): Promise<ChatPollResponse> {
    const url = new URL(`${this.apiBaseUrl}/api/chat/${sessionId}/poll/`);
    if (since) {
      url.searchParams.set('since', since);
    }

    const response = await fetch(url.toString(), {
      headers: this.getCommonHeaders(),
    });
    if (!response.ok) {
      throw new Error(`Failed to poll messages: ${response.statusText}`);
    }

    return response.json() as Promise<ChatPollResponse>;
  }

  startMessagePolling(
    sessionId: string,
    callbacks: MessagePollingCallbacks,
  ): MessagePollingHandle {
    const poll = async () => {
      try {
        const since = callbacks.getSince();
        const data = await this.fetchMessages(sessionId, since);
        if (data.messages.length > 0) {
          callbacks.onMessages(data.messages);
        }
      } catch (error) {
        if (callbacks.onError) {
          callbacks.onError(error instanceof Error ? error : new Error('Failed to poll messages'));
        }
      }
    };

    // perform an initial poll immediately
    void poll();

    this.messagePollingTimer = setInterval(() => {
      void poll();
    }, this.messagePollingIntervalMs);

    return {
      stop: () => this.stopMessagePolling(),
    };
  }

  stopMessagePolling(): void {
    if (this.messagePollingTimer) {
      clearInterval(this.messagePollingTimer);
      this.messagePollingTimer = undefined;
    }
  }

  private getJsonHeaders(): Record<string, string> {
    const headers = this.getCommonHeaders();
    headers['Content-Type'] = 'application/json'

    const csrfToken = this.csrfTokenProvider(this.apiBaseUrl);
    if (csrfToken) {
      headers['X-CSRFToken'] = csrfToken;
    }

    return headers;
  }

  private getCommonHeaders(): Record<string, string> {
    const headers: Record<string, string> = {
      'x-ocs-widget-version': this.widgetVersion,
    };

    if (this.embedKey) {
      headers['X-Embed-Key'] = this.embedKey;
    }

    return headers;
  }
}
