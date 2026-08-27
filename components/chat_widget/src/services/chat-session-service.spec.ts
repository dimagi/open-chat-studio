import { ChatSessionService, SessionAccessError, ChatAuthError, ConsentRequiredError } from './chat-session-service';

function progressMessage(content: string) {
  return {
    status: 'processing' as const,
    message: {
      created_at: new Date().toISOString(),
      role: 'assistant' as const,
      content,
      metadata: {},
      attachments: [],
    },
  };
}

function completeMessage(content: string) {
  return {
    status: 'complete' as const,
    message: {
      created_at: new Date().toISOString(),
      role: 'assistant' as const,
      content,
      attachments: [],
    },
  };
}

describe('ChatSessionService.getUploadHeaders', () => {
  it('includes the common headers and the CSRF token', () => {
    const service = new ChatSessionService({
      apiBaseUrl: 'https://example.com',
      widgetVersion: '1.0.0',
      embedKey: 'embed-1',
      sessionToken: 'tok-123',
      csrfTokenProvider: () => 'csrf-456',
    });

    expect(service.getUploadHeaders()).toEqual({
      'x-ocs-widget-version': '1.0.0',
      'X-Embed-Key': 'embed-1',
      'X-Session-Token': 'tok-123',
      'X-CSRFToken': 'csrf-456',
    });
  });

  it('omits the CSRF header when no token is available', () => {
    const service = new ChatSessionService({
      apiBaseUrl: 'https://example.com',
      widgetVersion: '1.0.0',
      csrfTokenProvider: () => undefined,
    });

    expect(service.getUploadHeaders()).toEqual({ 'x-ocs-widget-version': '1.0.0' });
  });
});

describe('ChatSessionService.pollTask', () => {
  let service: ChatSessionService;

  beforeEach(() => {
    service = new ChatSessionService({
      apiBaseUrl: 'https://example.com',
      widgetVersion: '1.0.0',
      taskPollingIntervalMs: 10,
      taskPollingMaxAttempts: 5,
    });
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('should call onProgress with message content when status is processing', async () => {
    const onMessage = jest.fn();
    const onProgress = jest.fn();

    let pollCount = 0;
    jest.spyOn(service, 'pollTaskOnce').mockImplementation(async () => {
      pollCount++;
      if (pollCount === 1) {
        return progressMessage('Searching...');
      }
      return completeMessage('Done');
    });

    const handle = service.pollTask('session-1', 'task-1', {
      onMessage,
      onProgress,
    });

    // Wait for both poll cycles to complete
    await new Promise(resolve => setTimeout(resolve, 50));
    handle.cancel();

    expect(onProgress).toHaveBeenCalledWith('Searching...');
    expect(onMessage).toHaveBeenCalledTimes(1);
    expect(onMessage.mock.calls[0][0].content).toBe('Done');
  });

  it('should not call onProgress when processing response has no message', async () => {
    const onMessage = jest.fn();
    const onProgress = jest.fn();

    let pollCount = 0;
    jest.spyOn(service, 'pollTaskOnce').mockImplementation(async () => {
      pollCount++;
      if (pollCount === 1) {
        return { status: 'processing' };
      }
      return completeMessage('Done');
    });

    const handle = service.pollTask('session-1', 'task-1', {
      onMessage,
      onProgress,
    });

    await new Promise(resolve => setTimeout(resolve, 50));
    handle.cancel();

    expect(onProgress).not.toHaveBeenCalled();
    expect(onMessage).toHaveBeenCalledTimes(1);
  });

  it('should call onProgress multiple times as progress updates arrive', async () => {
    const onMessage = jest.fn();
    const onProgress = jest.fn();

    let pollCount = 0;
    jest.spyOn(service, 'pollTaskOnce').mockImplementation(async () => {
      pollCount++;
      if (pollCount === 1) {
        return progressMessage('Step 1');
      }
      if (pollCount === 2) {
        return progressMessage('Step 2');
      }
      return completeMessage('Done');
    });

    const handle = service.pollTask('session-1', 'task-1', {
      onMessage,
      onProgress,
    });

    await new Promise(resolve => setTimeout(resolve, 100));
    handle.cancel();

    expect(onProgress).toHaveBeenCalledTimes(2);
    expect(onProgress).toHaveBeenNthCalledWith(1, 'Step 1');
    expect(onProgress).toHaveBeenNthCalledWith(2, 'Step 2');
    expect(onMessage).toHaveBeenCalledTimes(1);
  });

  it('should not call onProgress when processing message has empty content', async () => {
    const onMessage = jest.fn();
    const onProgress = jest.fn();

    let pollCount = 0;
    jest.spyOn(service, 'pollTaskOnce').mockImplementation(async () => {
      pollCount++;
      if (pollCount === 1) {
        return progressMessage('');
      }
      return completeMessage('Done');
    });

    const handle = service.pollTask('session-1', 'task-1', {
      onMessage,
      onProgress,
    });

    await new Promise(resolve => setTimeout(resolve, 50));
    handle.cancel();

    expect(onProgress).not.toHaveBeenCalled();
    expect(onMessage).toHaveBeenCalledTimes(1);
  });
});

describe('ChatSessionService.fetchAllMessages', () => {
  let service: ChatSessionService;

  function historyMessage(content: string, createdAt: string) {
    return {
      created_at: createdAt,
      role: 'assistant' as const,
      content,
      attachments: [],
    };
  }

  beforeEach(() => {
    service = new ChatSessionService({
      apiBaseUrl: 'https://example.com',
      widgetVersion: '1.0.0',
    });
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('returns messages from a single page', async () => {
    const fetchMock = jest.spyOn(service, 'fetchMessages').mockResolvedValue({
      messages: [historyMessage('a', '2026-01-01T00:00:01Z')],
      has_more: false,
      session_status: 'active',
    });

    const result = await service.fetchAllMessages('session-1');

    expect(result.map(m => m.content)).toEqual(['a']);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith('session-1', undefined);
  });

  it('pages through history until has_more is false', async () => {
    const fetchMock = jest
      .spyOn(service, 'fetchMessages')
      .mockResolvedValueOnce({
        messages: [historyMessage('a', '2026-01-01T00:00:01Z'), historyMessage('b', '2026-01-01T00:00:02Z')],
        has_more: true,
        session_status: 'active',
      })
      .mockResolvedValueOnce({
        messages: [historyMessage('c', '2026-01-01T00:00:03Z')],
        has_more: false,
        session_status: 'active',
      });

    const result = await service.fetchAllMessages('session-1');

    expect(result.map(m => m.content)).toEqual(['a', 'b', 'c']);
    expect(fetchMock).toHaveBeenNthCalledWith(1, 'session-1', undefined);
    expect(fetchMock).toHaveBeenNthCalledWith(2, 'session-1', '2026-01-01T00:00:02Z');
  });

  it('stops paging if the server keeps reporting has_more with empty pages', async () => {
    const fetchMock = jest.spyOn(service, 'fetchMessages').mockResolvedValue({
      messages: [],
      has_more: true,
      session_status: 'active',
    });

    const result = await service.fetchAllMessages('session-1');

    expect(result).toEqual([]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('stops paging at the safety cap and warns about truncation', async () => {
    const warnSpy = jest.spyOn(console, 'warn').mockImplementation(() => {});
    const maxPages = (ChatSessionService as any).MAX_HISTORY_PAGES as number;
    let page = 0;
    const fetchMock = jest.spyOn(service, 'fetchMessages').mockImplementation(async () => {
      page += 1;
      return {
        messages: [historyMessage(`m${page}`, `2026-01-01T00:00:${String(page).padStart(2, '0')}Z`)],
        has_more: true,
        session_status: 'active' as const,
      };
    });

    const result = await service.fetchAllMessages('session-1');

    expect(fetchMock).toHaveBeenCalledTimes(maxPages);
    expect(result).toHaveLength(maxPages);
    expect(warnSpy).toHaveBeenCalledWith('Chat history truncated after', maxPages, 'pages');
  });
});

describe('ChatSessionService.startMessagePolling', () => {
  let service: ChatSessionService;

  beforeEach(() => {
    service = new ChatSessionService({
      apiBaseUrl: 'https://example.com',
      widgetVersion: '1.0.0',
      messagePollingIntervalMs: 10,
    });
  });

  afterEach(() => {
    service.stopMessagePolling();
    jest.restoreAllMocks();
  });

  function pollResponse(messages: string[], sessionStatus: 'active' | 'ended' = 'active') {
    return {
      messages: messages.map(content => ({
        created_at: new Date().toISOString(),
        role: 'assistant' as const,
        content,
        attachments: [],
      })),
      has_more: false,
      session_status: sessionStatus,
    };
  }

  it('keeps polling while the session is active', async () => {
    const fetchMock = jest.spyOn(service, 'fetchMessages').mockResolvedValue(pollResponse([]));
    const onSessionEnded = jest.fn();

    service.startMessagePolling('session-1', {
      getSince: () => undefined,
      onMessages: jest.fn(),
      onSessionEnded,
    });
    await new Promise(resolve => setTimeout(resolve, 50));

    expect(fetchMock.mock.calls.length).toBeGreaterThan(1);
    expect(onSessionEnded).not.toHaveBeenCalled();
  });

  it('delivers final messages, stops polling, and reports an ended session', async () => {
    const fetchMock = jest.spyOn(service, 'fetchMessages').mockResolvedValue(pollResponse(['goodbye'], 'ended'));
    const onMessages = jest.fn();
    const onSessionEnded = jest.fn();

    service.startMessagePolling('session-1', {
      getSince: () => undefined,
      onMessages,
      onSessionEnded,
    });
    await new Promise(resolve => setTimeout(resolve, 50));

    expect(onMessages).toHaveBeenCalledTimes(1);
    expect(onMessages.mock.calls[0][0].map((m: { content: string }) => m.content)).toEqual(['goodbye']);
    expect(onSessionEnded).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('does not throw when an ended session is reported without an onSessionEnded callback', async () => {
    const fetchMock = jest.spyOn(service, 'fetchMessages').mockResolvedValue(pollResponse([], 'ended'));

    service.startMessagePolling('session-1', {
      getSince: () => undefined,
      onMessages: jest.fn(),
    });
    await new Promise(resolve => setTimeout(resolve, 50));

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe('ChatSessionService session tokens', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  function jsonResponse(body: unknown, init: { ok?: boolean; status?: number; statusText?: string } = {}) {
    return {
      ok: init.ok ?? true,
      status: init.status ?? 200,
      statusText: init.statusText ?? 'OK',
      json: () => Promise.resolve(body),
    } as Response;
  }

  function makeService() {
    return new ChatSessionService({ apiBaseUrl: 'https://example.com', widgetVersion: '1.0.0' });
  }

  it('captures the session token from the start response', async () => {
    const service = makeService();
    jest.spyOn(global, 'fetch').mockResolvedValue(jsonResponse({ session_id: 's1', session_token: 'tok-123', chatbot: {}, participant: {} }));

    const data = await service.startSession({ chatbot_id: 'c1' });

    expect(data.session_token).toBe('tok-123');
  });

  it('sends X-Session-Token on message requests once a token is set', async () => {
    const service = makeService();
    service.setSessionToken('tok-123');
    const fetchMock = jest.spyOn(global, 'fetch').mockResolvedValue(jsonResponse({ task_id: 't1', status: 'processing' }));

    await service.sendMessage('s1', { message: 'hi' });

    const headers = (fetchMock.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect(headers['X-Session-Token']).toBe('tok-123');
  });

  it('omits X-Session-Token when no token is set', async () => {
    const service = makeService();
    const fetchMock = jest.spyOn(global, 'fetch').mockResolvedValue(jsonResponse({ messages: [], has_more: false, session_status: 'active' }));

    await service.fetchMessages('s1');

    const headers = (fetchMock.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect(headers['X-Session-Token']).toBeUndefined();
  });

  it('throws SessionAccessError with the server code on 403', async () => {
    const service = makeService();
    jest.spyOn(global, 'fetch').mockResolvedValue(jsonResponse({ error: 'Session has expired', code: 'session_expired' }, { ok: false, status: 403, statusText: 'Forbidden' }));

    await expect(service.fetchMessages('s1')).rejects.toBeInstanceOf(SessionAccessError);
    await expect(service.fetchMessages('s1')).rejects.toMatchObject({ status: 403, code: 'session_expired' });
  });

  it('throws a plain Error on non-403 failures', async () => {
    const service = makeService();
    jest.spyOn(global, 'fetch').mockResolvedValue(jsonResponse({}, { ok: false, status: 500, statusText: 'Server Error' }));

    const error = await service.fetchMessages('s1').catch(e => e);
    expect(error).toBeInstanceOf(Error);
    expect(error).not.toBeInstanceOf(SessionAccessError);
  });

  it('surfaces the JSON error message on a non-403 failure', async () => {
    const service = makeService();
    jest.spyOn(global, 'fetch').mockResolvedValue(jsonResponse({ error: 'Server exploded' }, { ok: false, status: 500, statusText: 'Server Error' }));

    await expect(service.fetchMessages('s1')).rejects.toThrow('Server exploded');
  });

  it('falls back to statusText when the error body is not JSON', async () => {
    const service = makeService();
    jest.spyOn(global, 'fetch').mockResolvedValue({
      ok: false,
      status: 500,
      statusText: 'Server Error',
      json: () => Promise.reject(new Error('not json')),
    } as unknown as Response);

    await expect(service.fetchMessages('s1')).rejects.toThrow('Failed to poll messages: Server Error');
  });
});

describe('ChatSessionService consent', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  const consentBlock = { required: true, form_version_id: 7, text: '<p>Please agree</p>' };

  function jsonResponse(body: unknown, init: { ok?: boolean; status?: number; statusText?: string } = {}) {
    return {
      ok: init.ok ?? true,
      status: init.status ?? 200,
      statusText: init.statusText ?? 'OK',
      json: () => Promise.resolve(body),
    } as Response;
  }

  function makeService() {
    return new ChatSessionService({ apiBaseUrl: 'https://example.com', widgetVersion: '1.0.0' });
  }

  it('raises ConsentRequiredError, not SessionAccessError, on 403 consent_required', async () => {
    const service = makeService();
    jest
      .spyOn(global, 'fetch')
      .mockResolvedValue(
        jsonResponse({ error: 'Consent is required before chatting', code: 'consent_required', consent: consentBlock }, { ok: false, status: 403, statusText: 'Forbidden' }),
      );

    const failure = service.sendMessage('s1', { message: 'hi' });
    await expect(failure).rejects.toBeInstanceOf(ConsentRequiredError);
    await expect(failure).rejects.not.toBeInstanceOf(SessionAccessError);
    await expect(failure).rejects.toMatchObject({ consent: consentBlock });
  });

  it('still raises SessionAccessError for other 403 codes', async () => {
    const service = makeService();
    jest.spyOn(global, 'fetch').mockResolvedValue(jsonResponse({ error: 'Session has expired', code: 'session_expired' }, { ok: false, status: 403, statusText: 'Forbidden' }));

    await expect(service.sendMessage('s1', { message: 'hi' })).rejects.toBeInstanceOf(SessionAccessError);
  });

  it('posts the accepted form version and resolves on 204', async () => {
    const service = makeService();
    const fetchSpy = jest.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      status: 204,
      headers: new Headers(),
      json: () => Promise.reject(new Error('no body')),
    } as unknown as Response);

    await expect(service.recordConsent('s1', 7)).resolves.toBeUndefined();

    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('https://example.com/api/chat/s1/consent/');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({ form_version_id: 7 });
  });

  it('raises ConsentRequiredError with the current form on 409 consent_stale', async () => {
    const service = makeService();
    const current = { ...consentBlock, form_version_id: 8 };
    jest
      .spyOn(global, 'fetch')
      .mockResolvedValue(jsonResponse({ error: 'The consent form has changed', code: 'consent_stale', consent: current }, { ok: false, status: 409, statusText: 'Conflict' }));

    const failure = service.recordConsent('s1', 7);
    await expect(failure).rejects.toBeInstanceOf(ConsentRequiredError);
    await expect(failure).rejects.toMatchObject({ consent: current });
  });
});

describe('ChatSessionService deprecation headers', () => {
  const DOCS_URL = 'https://docs.openchatstudio.com/chat_widget/';
  let warnSpy: jest.SpyInstance;
  let errorSpy: jest.SpyInstance;

  beforeEach(() => {
    warnSpy = jest.spyOn(console, 'warn').mockImplementation(() => {});
    errorSpy = jest.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  function response(headers: Record<string, string>, body: unknown = { messages: [], has_more: false, session_status: 'active' }) {
    return {
      ok: true,
      status: 200,
      statusText: 'OK',
      headers: new Headers(headers),
      json: () => Promise.resolve(body),
    } as Response;
  }

  function makeService() {
    return new ChatSessionService({ apiBaseUrl: 'https://example.com', widgetVersion: '0.5.0' });
  }

  const sunsetHeaders = (sunset: string) => ({
    Deprecation: 'true',
    Sunset: sunset,
    Link: `<${DOCS_URL}>; rel="successor-version"`,
  });

  it('does not log when the response carries no deprecation header', async () => {
    const service = makeService();
    jest.spyOn(global, 'fetch').mockResolvedValue(response({}));

    await service.fetchMessages('s1');

    expect(warnSpy).not.toHaveBeenCalled();
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('warns when deprecated and the sunset date is in the future', async () => {
    const service = makeService();
    jest.spyOn(global, 'fetch').mockResolvedValue(response(sunsetHeaders('Wed, 01 Jan 2099 00:00:00 GMT')));

    await service.fetchMessages('s1');

    expect(errorSpy).not.toHaveBeenCalled();
    expect(warnSpy).toHaveBeenCalledTimes(1);
    const message = warnSpy.mock.calls[0][0] as string;
    expect(message).toContain('0.5.0');
    expect(message).toContain('deprecated');
    expect(message).toContain(DOCS_URL);
  });

  it('errors when the sunset date has already passed', async () => {
    const service = makeService();
    jest.spyOn(global, 'fetch').mockResolvedValue(response(sunsetHeaders('Sat, 01 Jan 2000 00:00:00 GMT')));

    await service.fetchMessages('s1');

    expect(warnSpy).not.toHaveBeenCalled();
    expect(errorSpy).toHaveBeenCalledTimes(1);
    const message = errorSpy.mock.calls[0][0] as string;
    expect(message).toContain('0.5.0');
    expect(message).toContain(DOCS_URL);
  });

  it('logs only once across repeated polls at the same level', async () => {
    const service = makeService();
    jest.spyOn(global, 'fetch').mockResolvedValue(response(sunsetHeaders('Wed, 01 Jan 2099 00:00:00 GMT')));

    await service.fetchMessages('s1');
    await service.fetchMessages('s1');
    await service.fetchMessages('s1');

    expect(warnSpy).toHaveBeenCalledTimes(1);
  });

  it('escalates from warning to error when the sunset date passes mid-session', async () => {
    const service = makeService();
    const sunsetMs = Date.parse('Wed, 01 Jan 2025 00:00:00 GMT');
    jest.spyOn(global, 'fetch').mockResolvedValue(response(sunsetHeaders('Wed, 01 Jan 2025 00:00:00 GMT')));
    const nowSpy = jest.spyOn(Date, 'now');

    nowSpy.mockReturnValue(sunsetMs - 1000);
    await service.fetchMessages('s1');

    nowSpy.mockReturnValue(sunsetMs + 1000);
    await service.fetchMessages('s1');

    expect(warnSpy).toHaveBeenCalledTimes(1);
    expect(errorSpy).toHaveBeenCalledTimes(1);
  });

  it('detects the headers on the session-start response too', async () => {
    const service = makeService();
    jest.spyOn(global, 'fetch').mockResolvedValue(response(sunsetHeaders('Wed, 01 Jan 2099 00:00:00 GMT'), { session_id: 's1', chatbot: {}, participant: {} }));

    await service.startSession({ chatbot_id: 'c1' });

    expect(warnSpy).toHaveBeenCalledTimes(1);
  });
});

describe('ChatSessionService auth token', () => {
  const startUrl = 'https://example.com/api/chat/start/';
  let fetchMock: jest.Mock;

  function okResponse() {
    return {
      ok: true,
      status: 201,
      headers: { get: () => null },
      json: () => Promise.resolve({ session_id: 's-1', chatbot: {}, participant: {} }),
    } as unknown as Response;
  }

  function deniedResponse() {
    return {
      ok: false,
      status: 401,
      statusText: 'Unauthorized',
      headers: { get: () => null },
      json: () => Promise.resolve({ error: 'Authentication required to chat with this chatbot', code: 'chat_access_denied' }),
    } as unknown as Response;
  }

  function service(options: Partial<ConstructorParameters<typeof ChatSessionService>[0]> = {}) {
    return new ChatSessionService({
      apiBaseUrl: 'https://example.com',
      widgetVersion: '1.0.0',
      csrfTokenProvider: () => undefined,
      ...options,
    });
  }

  function headersOf(call: number): Record<string, string> {
    return fetchMock.mock.calls[call][1].headers;
  }

  beforeEach(() => {
    fetchMock = jest.fn().mockResolvedValue(okResponse());
    global.fetch = fetchMock as unknown as typeof fetch;
  });

  it('sends the provider token as a bearer credential on start', async () => {
    await service({ authTokenProvider: () => 'tok-abc' }).startSession({});

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(startUrl);
    expect(headersOf(0)['Authorization']).toBe('Bearer tok-abc');
  });

  it('omits the header entirely when there is no provider', async () => {
    await service().startSession({});

    expect(headersOf(0)).not.toHaveProperty('Authorization');
  });

  it('omits the header when the provider yields nothing', async () => {
    await service({ authTokenProvider: () => undefined }).startSession({});

    expect(headersOf(0)).not.toHaveProperty('Authorization');
  });

  it('asks the provider afresh for every session start', async () => {
    // No stored token: a credential the service held onto would go stale without
    // anything noticing.
    const provider = jest.fn().mockResolvedValueOnce('tok-1').mockResolvedValueOnce('tok-2');
    const svc = service({ authTokenProvider: provider });

    await svc.startSession({});
    await svc.startSession({});

    expect(provider).toHaveBeenCalledTimes(2);
    expect(headersOf(0)['Authorization']).toBe('Bearer tok-1');
    expect(headersOf(1)['Authorization']).toBe('Bearer tok-2');
  });

  it('keeps the bearer token off session-bound requests', async () => {
    const svc = service({ authTokenProvider: () => 'tok-abc', sessionToken: 'sess-1' });

    expect(svc.getUploadHeaders()).not.toHaveProperty('Authorization');

    await svc.pollTaskOnce('s-1', 't-1');
    await svc.sendMessage('s-1', {});
    await svc.fetchMessages('s-1');

    for (let call = 0; call < fetchMock.mock.calls.length; call++) {
      expect(headersOf(call)).not.toHaveProperty('Authorization');
    }
  });

  it('passes forceRefresh false on the first ask', async () => {
    const provider = jest.fn().mockResolvedValue('tok-fresh');
    await service({ authTokenProvider: provider }).startSession({});

    expect(provider).toHaveBeenCalledWith({ forceRefresh: false });
  });

  it('accepts a synchronous provider', async () => {
    await service({ authTokenProvider: () => 'tok-sync' }).startSession({});

    expect(headersOf(0)['Authorization']).toBe('Bearer tok-sync');
  });

  it('retries once with forceRefresh when the first token is rejected', async () => {
    fetchMock.mockResolvedValueOnce(deniedResponse()).mockResolvedValueOnce(okResponse());
    const provider = jest.fn().mockResolvedValueOnce('tok-cached').mockResolvedValueOnce('tok-fresh');

    const result = await service({ authTokenProvider: provider }).startSession({});

    expect(result.session_id).toBe('s-1');
    expect(provider.mock.calls).toEqual([[{ forceRefresh: false }], [{ forceRefresh: true }]]);
    expect(headersOf(0)['Authorization']).toBe('Bearer tok-cached');
    expect(headersOf(1)['Authorization']).toBe('Bearer tok-fresh');
  });

  it('gives up after one retry rather than looping', async () => {
    fetchMock.mockResolvedValue(deniedResponse());
    const provider = jest.fn().mockResolvedValueOnce('tok-cached').mockResolvedValueOnce('tok-fresh');

    await expect(service({ authTokenProvider: provider }).startSession({})).rejects.toThrow(ChatAuthError);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(provider).toHaveBeenCalledTimes(2);
  });

  it('does not resend a token the refresh did not change', async () => {
    fetchMock.mockResolvedValue(deniedResponse());
    const provider = jest.fn().mockResolvedValue('tok-rejected');

    await expect(service({ authTokenProvider: provider }).startSession({})).rejects.toThrow(ChatAuthError);
    expect(provider).toHaveBeenCalledTimes(2);
    // The refresh produced the same token, so the retry would have been an
    // identical request against a throttled endpoint.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('retries an unauthenticated start once the provider can mint a token', async () => {
    // The provider was not ready when the user first sent, so the request went out
    // without a credential; the refresh is what makes it recoverable.
    fetchMock.mockResolvedValueOnce(deniedResponse()).mockResolvedValueOnce(okResponse());
    const provider = jest.fn().mockResolvedValueOnce(undefined).mockResolvedValueOnce('tok-ready');

    await service({ authTokenProvider: provider }).startSession({});

    expect(headersOf(0)).not.toHaveProperty('Authorization');
    expect(headersOf(1)['Authorization']).toBe('Bearer tok-ready');
  });

  it('does not retry when there is no provider to mint a fresh token', async () => {
    fetchMock.mockResolvedValue(deniedResponse());

    await expect(service().startSession({})).rejects.toThrow(ChatAuthError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('raises ChatAuthError carrying the server error and code', async () => {
    fetchMock.mockResolvedValue(deniedResponse());

    await expect(service().startSession({})).rejects.toMatchObject({
      name: 'ChatAuthError',
      status: 401,
      code: 'chat_access_denied',
      message: 'Authentication required to chat with this chatbot',
    });
  });

  it('does not confuse a 401 with a 403 rejection of an existing session', async () => {
    fetchMock.mockResolvedValue(deniedResponse());

    const error = await service()
      .startSession({})
      .catch(e => e);
    expect(error).toBeInstanceOf(ChatAuthError);
    expect(error).not.toBeInstanceOf(SessionAccessError);
  });

  it('reports a provider that throws as an auth failure rather than a generic error', async () => {
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => {});
    const thrown = new Error('mint endpoint down for tok-secret');
    const provider = jest.fn().mockRejectedValue(thrown);

    await expect(service({ authTokenProvider: provider }).startSession({})).rejects.toMatchObject({
      name: 'ChatAuthError',
      code: 'auth_token_unavailable',
      // Generic on purpose: this message is shown in the transcript and persisted,
      // so the host's exception text must not ride along into localStorage.
      message: 'Could not obtain an authentication token',
    });
    expect(fetchMock).not.toHaveBeenCalled();
    // ...but it is still available to whoever is debugging the integration.
    expect(consoleError).toHaveBeenCalledWith('[open-chat-studio-widget] authTokenProvider failed', thrown);
    consoleError.mockRestore();
  });

  it('picks up a replaced provider without rebuilding the service', async () => {
    const svc = service({ authTokenProvider: () => 'tok-old' });
    await svc.startSession({});
    svc.setAuthTokenProvider(() => 'tok-new');
    await svc.startSession({});

    expect(headersOf(0)['Authorization']).toBe('Bearer tok-old');
    expect(headersOf(1)['Authorization']).toBe('Bearer tok-new');
  });

  it('stops sending a credential when the provider is removed', async () => {
    const svc = service({ authTokenProvider: () => 'tok-old' });
    await svc.startSession({});
    svc.setAuthTokenProvider(undefined);
    await svc.startSession({});

    expect(headersOf(0)['Authorization']).toBe('Bearer tok-old');
    expect(headersOf(1)).not.toHaveProperty('Authorization');
  });

  it('picks up a provider installed after construction', async () => {
    const svc = service();
    svc.setAuthTokenProvider(() => 'tok-late');
    await svc.startSession({});

    expect(headersOf(0)['Authorization']).toBe('Bearer tok-late');
  });
});
