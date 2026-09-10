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

/**
 * The server refused to admit a new chat session (401). Raised only by
 * `startSession`, where the chatbot's channel requires a credential the widget
 * did not present, or presented stale: an OAuth bearer token on an `oauth`-mode
 * channel, or the embed key on an `embed_key`-mode one.
 *
 * Distinct from `SessionAccessError` (403), which rejects an *existing*
 * session's token and means the conversation is over. This one means the
 * conversation never started, so there is nothing to discard.
 */
export class ChatAuthError extends Error {
  readonly status: number;
  readonly code?: string;

  constructor(status: number, code: string | undefined, message: string) {
    super(message);
    this.name = 'ChatAuthError';
    this.status = status;
    this.code = code;
  }
}

/**
 * The server holds the message until the participant accepts the chatbot's consent
 * form (403 `consent_required`), or refuses a stale acceptance (409 `consent_stale`).
 * Either way `consent` is the block to render; the session itself is still good.
 */
export class ConsentRequiredError extends Error {
  readonly consent: ChatConsent;

  constructor(consent: ChatConsent, message: string) {
    super(message);
    this.name = 'ConsentRequiredError';
    this.consent = consent;
  }
}

/**
 * Supplies the OAuth bearer token for `chat/start/` and for renewing a session
 * token. Called once per session start or renewal, and a second time with
 * `forceRefresh` when the server rejected the first token.
 */
export type AuthTokenProvider = (context: { forceRefresh: boolean }) => string | undefined | Promise<string | undefined>;

/** Called when the service has renewed the session token, so the host can persist the new one. */
export type SessionTokenRenewedCallback = (sessionId: string, token: string, expiresAt: string) => void;

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

/**
 * The published version's consent form as the server describes it. `required` is
 * false when the chatbot has no form, or the participant has already accepted it.
 * Older backends omit the block entirely.
 */
export interface ChatConsent {
  required: boolean;
  form_version_id: number | null;
  text: string | null;
}

export interface ChatStartSessionResponse {
  session_id: string;
  session_token?: string | null;
  /** When `session_token` stops working (ISO 8601). Null when no token was issued; older backends omit it. */
  expires_at?: string | null;
  chatbot: unknown;
  participant: unknown;
  consent?: ChatConsent;
}

export interface ChatSessionTokenResponse {
  session_id: string;
  session_token: string;
  expires_at: string;
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
  consent?: ChatConsent;
}

export interface ChatSessionServiceOptions {
  apiBaseUrl: string;
  embedKey?: string;
  widgetVersion: string;
  sessionToken?: string;
  /** When `sessionToken` stops working (ISO 8601). Unknown when omitted. */
  sessionTokenExpiresAt?: string;
  authTokenProvider?: AuthTokenProvider;
  onSessionTokenRenewed?: SessionTokenRenewedCallback;
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
  /** Called once when the server reports the session has ended; polling stops first. */
  onSessionEnded?: () => void;
  /** Called on every poll whose response carries a consent block. */
  onConsent?: (consent: ChatConsent) => void;
  onError?: (error: Error) => void;
}

export interface MessagePollingHandle {
  stop: () => void;
}

/** Refusal codes that carry a consent block and leave the session usable. */
const CONSENT_REFUSAL_CODES = ['consent_required', 'consent_stale'];

/** The 403 code for a session token that has aged out; the only refusal renewal can answer. */
const SESSION_EXPIRED_CODE = 'session_expired';

interface ErrorBody {
  message: string;
  code?: string;
  consent?: ChatConsent;
}

export class ChatSessionService {
  private readonly apiBaseUrl: string;
  private readonly embedKey?: string;
  private readonly widgetVersion: string;
  private sessionToken?: string;
  private sessionTokenExpiresAt?: number;
  private authTokenProvider?: AuthTokenProvider;
  private readonly onSessionTokenRenewed?: SessionTokenRenewedCallback;
  private renewalInFlight?: Promise<boolean>;
  private readonly csrfTokenProvider: (apiBaseUrl: string) => string | undefined;
  private readonly taskPollingIntervalMs: number;
  private readonly taskPollingMaxAttempts: number;
  private readonly messagePollingIntervalMs: number;
  private messagePollingTimer?: ReturnType<typeof setInterval>;
  private loggedSunsetLevel?: 'warn' | 'error';
  private static readonly MAX_HISTORY_PAGES = 40;
  /**
   * Renew this long before the token's stated expiry. Absorbs the request's own
   * latency and modest clock skew; larger skew is caught by the retry on a
   * `session_expired` refusal.
   */
  private static readonly SESSION_TOKEN_RENEW_LEAD_MS = 60_000;

  constructor(options: ChatSessionServiceOptions) {
    this.apiBaseUrl = options.apiBaseUrl;
    this.embedKey = options.embedKey;
    this.widgetVersion = options.widgetVersion;
    this.setSessionToken(options.sessionToken, options.sessionTokenExpiresAt);
    this.authTokenProvider = options.authTokenProvider;
    this.onSessionTokenRenewed = options.onSessionTokenRenewed;
    this.csrfTokenProvider = options.csrfTokenProvider ?? getCSRFToken;
    this.taskPollingIntervalMs = options.taskPollingIntervalMs ?? 1000;
    this.taskPollingMaxAttempts = options.taskPollingMaxAttempts ?? 120;
    this.messagePollingIntervalMs = options.messagePollingIntervalMs ?? 30000;
  }

  async startSession(requestBody: Record<string, unknown>): Promise<ChatStartSessionResponse> {
    const response = await this.requestWithBearer(authToken =>
      this.request(`${this.apiBaseUrl}/api/chat/start/`, {
        method: 'POST',
        headers: this.getStartHeaders(authToken),
        body: JSON.stringify(requestBody),
      }),
    );

    if (!response.ok) {
      await this.raiseForStatus(response, 'Failed to start session');
    }

    return response.json() as Promise<ChatStartSessionResponse>;
  }

  /**
   * Replace the session token with a fresh one from `chat/<id>/token/`, which
   * admits the same bearer credential as `chat/start/`. The new token is applied
   * to this service and reported through `onSessionTokenRenewed`.
   */
  async renewSessionToken(sessionId: string): Promise<ChatSessionTokenResponse> {
    const tokenBeforeRenewal = this.sessionToken;
    const response = await this.requestWithBearer(authToken =>
      this.request(`${this.apiBaseUrl}/api/chat/${sessionId}/token/`, {
        method: 'POST',
        headers: this.getStartHeaders(authToken),
      }),
    );

    if (!response.ok) {
      await this.raiseForStatus(response, 'Failed to renew session token');
    }

    const data = (await response.json()) as ChatSessionTokenResponse;
    // A session cleared or replaced while the renewal was in flight keeps its own token.
    if (this.sessionToken === tokenBeforeRenewal) {
      this.setSessionToken(data.session_token, data.expires_at);
      this.onSessionTokenRenewed?.(sessionId, data.session_token, data.expires_at);
    }
    return data;
  }

  /**
   * Renew the session token if it is about to expire and a provider can supply the
   * bearer credential. Resolves either way: a failed renewal is logged, and the
   * request that follows is refused by the server if the token really has lapsed,
   * which then takes the `session_expired` recovery path.
   */
  async refreshSessionTokenIfExpiring(sessionId: string): Promise<void> {
    if (this.sessionTokenExpiring()) {
      await this.tryRenewSessionToken(sessionId);
    }
  }

  private sessionTokenExpiring(): boolean {
    if (!this.canRenewSessionToken() || this.sessionTokenExpiresAt === undefined) {
      return false;
    }
    return this.sessionTokenExpiresAt - Date.now() <= ChatSessionService.SESSION_TOKEN_RENEW_LEAD_MS;
  }

  private canRenewSessionToken(): boolean {
    return Boolean(this.authTokenProvider && this.sessionToken);
  }

  /** Renew once for however many requests notice the expiry at the same time. */
  private tryRenewSessionToken(sessionId: string): Promise<boolean> {
    if (!this.renewalInFlight) {
      this.renewalInFlight = this.renewSessionToken(sessionId)
        .then(() => true)
        .catch(error => {
          console.warn('[open-chat-studio-widget] session token renewal failed', error);
          return false;
        })
        .finally(() => {
          this.renewalInFlight = undefined;
        });
    }
    return this.renewalInFlight;
  }

  /**
   * Send a request authorised by the session token, renewing that token first
   * when it is due, and once more if the server reports it expired anyway.
   * `init` is a factory so the retry picks up the renewed token's headers.
   */
  private async sessionRequest(sessionId: string, url: string, init: () => RequestInit, fallbackPrefix: string): Promise<Response> {
    await this.refreshSessionTokenIfExpiring(sessionId);
    const response = await this.request(url, init());
    if (response.ok) {
      return response;
    }

    const body = await this.readErrorBody(response, fallbackPrefix);
    if (response.status === 403 && body.code === SESSION_EXPIRED_CODE && this.canRenewSessionToken() && (await this.tryRenewSessionToken(sessionId))) {
      const retried = await this.request(url, init());
      if (!retried.ok) {
        await this.raiseForStatus(retried, fallbackPrefix);
      }
      return retried;
    }
    throw this.errorFor(response.status, body);
  }

  async sendMessage(sessionId: string, payload: Record<string, unknown>): Promise<ChatSendMessageResponse> {
    const response = await this.sessionRequest(
      sessionId,
      `${this.apiBaseUrl}/api/chat/${sessionId}/message/`,
      () => ({
        method: 'POST',
        headers: this.getJsonHeaders(),
        body: JSON.stringify(payload),
      }),
      'Failed to send message',
    );

    return response.json() as Promise<ChatSendMessageResponse>;
  }

  /**
   * Record the participant's acceptance of the consent form version the server
   * last described. Resolves on 204; rejects with `ConsentRequiredError` carrying
   * the current block when the form has changed since (409 `consent_stale`).
   */
  async recordConsent(sessionId: string, formVersionId: number): Promise<void> {
    await this.sessionRequest(
      sessionId,
      `${this.apiBaseUrl}/api/chat/${sessionId}/consent/`,
      () => ({
        method: 'POST',
        headers: this.getJsonHeaders(),
        body: JSON.stringify({ form_version_id: formVersionId }),
      }),
      'Failed to record consent',
    );
  }

  async pollTaskOnce(sessionId: string, taskId: string): Promise<ChatTaskPollResponse> {
    const response = await this.sessionRequest(
      sessionId,
      `${this.apiBaseUrl}/api/chat/${sessionId}/${taskId}/poll/`,
      () => ({ headers: this.getCommonHeaders() }),
      'Failed to poll task',
    );

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

    const response = await this.sessionRequest(sessionId, url.toString(), () => ({ headers: this.getCommonHeaders() }), 'Failed to poll messages');

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
        if (data.consent) {
          callbacks.onConsent?.(data.consent);
        }
        if (data.session_status === 'ended') {
          this.stopMessagePolling();
          callbacks.onSessionEnded?.();
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

  /** `expiresAt` is ISO 8601; leave it out when the expiry is unknown, and renewal waits for a refusal. */
  setSessionToken(token?: string, expiresAt?: string | null): void {
    this.sessionToken = token;
    const parsed = token && expiresAt ? Date.parse(expiresAt) : NaN;
    this.sessionTokenExpiresAt = Number.isNaN(parsed) ? undefined : parsed;
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
    throw this.errorFor(response.status, await this.readErrorBody(response, fallbackPrefix));
  }

  private errorFor(status: number, { message, code, consent }: ErrorBody): Error {
    // Before the generic 403: a consent refusal keeps the session, a token refusal discards it.
    if (consent && CONSENT_REFUSAL_CODES.includes(code)) {
      return new ConsentRequiredError(consent, message);
    }
    if (status === 403) {
      return new SessionAccessError(status, code, message);
    }
    if (status === 401) {
      return new ChatAuthError(status, code, message);
    }
    return new Error(message);
  }

  /** The server's error wording and codes, falling back to the status text on a non-JSON body. */
  private async readErrorBody(response: Response, fallbackPrefix: string): Promise<ErrorBody> {
    try {
      const data = (await response.json()) as { error?: string; code?: string; consent?: ChatConsent };
      return {
        message: data?.error || `${fallbackPrefix}: ${response.statusText}`,
        code: data?.code,
        consent: data?.consent,
      };
    } catch {
      return { message: `${fallbackPrefix}: ${response.statusText}` };
    }
  }

  /** Headers for multipart requests (no Content-Type — fetch sets the boundary). */
  getUploadHeaders(): Record<string, string> {
    const headers = this.getCommonHeaders();

    const csrfToken = this.csrfTokenProvider(this.apiBaseUrl);
    if (csrfToken) {
      headers['X-CSRFToken'] = csrfToken;
    }

    return headers;
  }

  private getJsonHeaders(): Record<string, string> {
    const headers = this.getUploadHeaders();
    headers['Content-Type'] = 'application/json';
    return headers;
  }

  setAuthTokenProvider(provider?: AuthTokenProvider): void {
    this.authTokenProvider = provider;
  }

  /**
   * Headers for the two requests the host's bearer token admits: `chat/start/`
   * and `chat/<id>/token/`. Every other request is authorised by the session
   * token instead.
   */
  private getStartHeaders(authToken?: string): Record<string, string> {
    const headers = this.getJsonHeaders();
    if (authToken) {
      headers['Authorization'] = `Bearer ${authToken}`;
    }
    return headers;
  }

  /**
   * Send a bearer-authorised request. The provider may have handed back a cached
   * token that has since expired, so on a 401 ask again with `forceRefresh` before
   * giving up. One retry only, and only for a token that actually changed.
   */
  private async requestWithBearer(send: (authToken?: string) => Promise<Response>): Promise<Response> {
    const token = await this.resolveAuthToken(false);
    let response = await send(token);

    if (response.status === 401 && this.authTokenProvider) {
      const refreshed = await this.resolveAuthToken(true);
      if (refreshed && refreshed !== token) {
        response = await send(refreshed);
      }
    }
    return response;
  }

  /**
   * Asks the host for a bearer token at the moment one is needed.
   */
  private async resolveAuthToken(forceRefresh: boolean): Promise<string | undefined> {
    if (!this.authTokenProvider) {
      return undefined;
    }
    try {
      return (await this.authTokenProvider({ forceRefresh })) || undefined;
    } catch (error) {
      // The host's exception text stays out of the thrown message: it surfaces as a
      // chat message and is persisted to localStorage with the transcript, so an
      // error that quotes a token would write that token to disk.
      console.error('[open-chat-studio-widget] authTokenProvider failed', error);
      throw new ChatAuthError(401, 'auth_token_unavailable', 'Could not obtain an authentication token');
    }
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
