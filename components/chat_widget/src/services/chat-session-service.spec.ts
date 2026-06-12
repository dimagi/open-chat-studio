import { ChatSessionService, SessionAccessError } from './chat-session-service';

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
