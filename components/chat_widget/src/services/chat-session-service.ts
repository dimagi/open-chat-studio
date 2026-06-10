import { getCSRFToken } from '../utils/cookies';

export class SessionAccessError extends Error {
  readonly status: number;
  readonly code?: string;

  constructor(status: number, code: string | undefined, message: string) {
    super(message);
    this.name = 'SessionAccessError';
    this.status = status;
    this.code = code;
  }
}

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
  session_token?: string | null;
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
  sessionToken?: string;
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
  private sessionToken?: string;
  private readonly csrfTokenProvider: (apiBaseUrl: string) => string | undefined;
  private readonly taskPollingIntervalMs: number;
  private readonly taskPollingMaxAttempts: number;
  private readonly messagePollingIntervalMs: number;
  private messagePollingTimer?: ReturnType<typeof setInterval>;
  private loggedSunsetLevel?: 'warn' | 'error';
  private static readonly MAX_HISTORY_PAGES = 40;

  constructor(options: ChatSessionServiceOptions) {
    this.apiBaseUrl = options.apiBaseUrl;
    this.embedKey = options.embedKey;
    this.widgetVersion = options.widgetVersion;
    this.sessionToken = options.sessionToken;
    this.csrfTokenProvider = options.csrfTokenProvider ?? getCSRFToken;
    this.taskPollingIntervalMs = options.taskPollingIntervalMs ?? 1000;
    this.taskPollingMaxAttempts = options.taskPollingMaxAttempts ?? 120;
    this.messagePollingIntervalMs = options.messagePollingIntervalMs ?? 30000;
  }

  async startSession(requestBody: Record<string, unknown>): Promise<ChatStartSessionResponse> {
    const response = await this.request(`${this.apiBaseUrl}/api/chat/start/`, {
      method: 'POST',
      headers: this.getJsonHeaders(),
      body: JSON.stringify(requestBody),
    });

    if (!response.ok) {
      await this.raiseForStatus(response, 'Failed to start session');
    }

    return response.json() as Promise<ChatStartSessionResponse>;
  }

  async sendMessage(sessionId: string, payload: Record<string, unknown>): Promise<ChatSendMessageResponse> {
    const response = await this.request(`${this.apiBaseUrl}/api/chat/${sessionId}/message/`, {
      method: 'POST',
      headers: this.getJsonHeaders(),
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      await this.raiseForStatus(response, 'Failed to send message');
    }

    return response.json() as Promise<ChatSendMessageResponse>;
  }

  async pollTaskOnce(sessionId: string, taskId: string): Promise<ChatTaskPollResponse> {
    const response = await this.request(`${this.apiBaseUrl}/api/chat/${sessionId}/${taskId}/poll/`, {
      headers: this.getCommonHeaders(),
    });

    if (!response.ok) {
      await this.raiseForStatus(response, 'Failed to poll task');
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

    const response = await this.request(url.toString(), {
      headers: this.getCommonHeaders(),
    });
    if (!response.ok) {
      await this.raiseForStatus(response, 'Failed to poll messages');
    }

    return response.json() as Promise<ChatPollResponse>;
  }

  /**
   * Fetch the complete message history for a session by paging through the
   * poll endpoint until no more messages remain.
   */
  async fetchAllMessages(sessionId: string): Promise<ChatMessage[]> {
    const allMessages: ChatMessage[] = [];
    let since: string | undefined;
    let hasMore = true;

    for (let page = 0; hasMore && page < ChatSessionService.MAX_HISTORY_PAGES; page++) {
      const data = await this.fetchMessages(sessionId, since);
      allMessages.push(...data.messages);
      hasMore = data.has_more && data.messages.length > 0;
      // The server returns pages in ascending created_at order and `since` is
      // exclusive (created_at > since), so the last message's timestamp is the
      // next page cursor.
      since = data.messages.at(-1)?.created_at;
    }

    if (hasMore) {
      console.warn('Chat history truncated after', ChatSessionService.MAX_HISTORY_PAGES, 'pages');
    }

    return allMessages;
  }

  startMessagePolling(sessionId: string, callbacks: MessagePollingCallbacks): MessagePollingHandle {
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

  setSessionToken(token?: string): void {
    this.sessionToken = token;
  }

  private async request(input: string, init?: RequestInit): Promise<Response> {
    const response = await fetch(input, init);
    this.checkSunsetHeaders(response);
    return response;
  }

  /**
   * Log a deprecation warning (RFC 8594 `Deprecation`/`Sunset`/`Link` headers)
   * when the server reports that this widget version is deprecated. Warns
   * during the deprecation window and errors once the sunset date has passed.
   * Logs at most once per level so polling does not flood the console.
   */
  private checkSunsetHeaders(response: Response): void {
    const headers = response?.headers;
    if (!headers || typeof headers.get !== 'function') {
      return;
    }
    if (headers.get('Deprecation') !== 'true') {
      return;
    }

    const sunsetAt = this.parseSunsetDate(headers.get('Sunset'));
    const pastSunset = sunsetAt !== null && Date.now() >= sunsetAt.getTime();
    const level: 'warn' | 'error' = pastSunset ? 'error' : 'warn';
    if (this.loggedSunsetLevel === level) {
      return;
    }
    this.loggedSunsetLevel = level;

    const upgradeUrl = this.parseSuccessorUrl(headers.get('Link'));
    const upgradeSuffix = upgradeUrl ? ` Upgrade: ${upgradeUrl}` : '';
    const sunsetText = sunsetAt ? sunsetAt.toUTCString() : 'an upcoming date';
    if (level === 'error') {
      console.error(`[open-chat-studio-widget] Widget version ${this.widgetVersion} is past its sunset date ` + `(${sunsetText}) and may stop working.${upgradeSuffix}`);
    } else {
      console.warn(`[open-chat-studio-widget] Widget version ${this.widgetVersion} is deprecated and will stop ` + `working after ${sunsetText}.${upgradeSuffix}`);
    }
  }

  private parseSunsetDate(sunset: string | null): Date | null {
    if (!sunset) {
      return null;
    }
    const parsed = new Date(sunset);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }

  private parseSuccessorUrl(link: string | null): string | undefined {
    const match = link?.match(/<([^>]+)>\s*;\s*rel="?successor-version"?/);
    return match?.[1];
  }

  private async raiseForStatus(response: Response, fallbackPrefix: string): Promise<never> {
    let message = `${fallbackPrefix}: ${response.statusText}`;
    let code: string | undefined;
    try {
      const data = (await response.json()) as { error?: string; code?: string };
      if (data?.error) {
        message = data.error;
      }
      code = data?.code;
    } catch {
      // non-JSON body; keep statusText fallback
    }
    if (response.status === 403) {
      throw new SessionAccessError(response.status, code, message);
    }
    throw new Error(message);
  }

  private getJsonHeaders(): Record<string, string> {
    const headers = this.getCommonHeaders();
    headers['Content-Type'] = 'application/json';

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

    if (this.sessionToken) {
      headers['X-Session-Token'] = this.sessionToken;
    }

    return headers;
  }
}
