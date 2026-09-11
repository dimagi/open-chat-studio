import { newSpecPage, SpecPage } from '@stencil/core/testing';
import { OcsChat } from './ocs-chat';

/**
 * These drive the real ChatSessionService and assert on the requests it makes,
 * so the header wiring is covered end to end rather than against a hand-written
 * mock of the service that could drift from it.
 */
describe('ocs-chat auth token provider', () => {
  let fetchMock: jest.Mock;
  let startDenied: boolean;
  let renewDenied: boolean;
  /** Uploads refused with `session_expired` before one is accepted. */
  let uploadRefusals: number;
  /** `expires_at` on the start response; a near expiry makes the first session-bound request renew. */
  let tokenLifetimeSeconds: number;

  function jsonResponse(status: number, body: unknown) {
    return {
      ok: status < 400,
      status,
      statusText: String(status),
      headers: { get: () => null },
      json: () => Promise.resolve(body),
    } as unknown as Response;
  }

  const secondsFromNow = (seconds: number) => new Date(Date.now() + seconds * 1000).toISOString();

  /** Requests the widget makes when the user sends the first message. */
  function router(url: string) {
    if (url.includes('/api/chat/start/')) {
      if (startDenied) {
        return jsonResponse(401, { error: 'Authentication required to chat with this chatbot', code: 'chat_access_denied' });
      }
      return jsonResponse(201, { session_id: 'session-1', session_token: 'sess-tok', expires_at: secondsFromNow(tokenLifetimeSeconds), chatbot: {}, participant: {} });
    }
    if (url.includes('/token/')) {
      if (renewDenied) {
        return jsonResponse(401, { error: 'Authentication required to chat with this chatbot', code: 'chat_access_denied' });
      }
      return jsonResponse(200, { session_id: 'session-1', session_token: 'sess-tok-2', expires_at: secondsFromNow(3600) });
    }
    if (url.includes('/upload/')) {
      if (uploadRefusals > 0) {
        uploadRefusals -= 1;
        return jsonResponse(403, { error: 'Session has expired', code: 'session_expired' });
      }
      return jsonResponse(201, { files: [{ id: 42, name: 'a.txt', size: 5, content_type: 'text/plain' }] });
    }
    if (url.includes('/message/')) {
      return jsonResponse(200, { task_id: 'task-1', status: 'processing' });
    }
    return jsonResponse(200, { messages: [], has_more: false, session_status: 'active' });
  }

  /** Headers of the nth call to `chat/start/`. */
  function startHeaders(n = 0): Record<string, string> {
    return headersOf('/api/chat/start/', n);
  }

  function headersOf(path: string, n = 0): Record<string, string> {
    const calls = fetchMock.mock.calls.filter(([url]) => String(url).includes(path));
    return calls[n]?.[1]?.headers;
  }

  function storedValue(key: string): string | undefined {
    const writes = (window.localStorage.setItem as jest.Mock).mock.calls.filter(([k]) => k === key);
    return writes.at(-1)?.[1];
  }

  async function widget(html: string) {
    const page = await newSpecPage({ components: [OcsChat], html });
    await page.waitForChanges();
    return page;
  }

  async function send(page: SpecPage, message = 'hello') {
    await page.rootInstance.sendMessage(message);
    await page.waitForChanges();
  }

  beforeEach(() => {
    startDenied = false;
    renewDenied = false;
    uploadRefusals = 0;
    tokenLifetimeSeconds = 3600;
    fetchMock = jest.fn((url: string) => Promise.resolve(router(url)));
    global.fetch = fetchMock as unknown as typeof fetch;
    Object.defineProperty(window, 'localStorage', {
      value: { getItem: jest.fn(() => null), setItem: jest.fn(), removeItem: jest.fn(), clear: jest.fn() },
      writable: true,
    });
    // Participant id generation needs it, and the spec environment has no crypto.
    Object.defineProperty(window, 'crypto', {
      value: { getRandomValues: (arr: Uint8Array) => arr.fill(7) },
      writable: true,
    });
  });

  /** Sets the provider the way an embedding page does -- a property on the element. */
  async function installProvider(page: SpecPage, provider: unknown) {
    (page.root as any).authTokenProvider = provider;
    await page.waitForChanges();
  }

  it('sends the provider token as a bearer credential', async () => {
    const provider = jest.fn().mockResolvedValue('tok-abc');
    const page = await widget('<open-chat-studio-widget chatbot-id="bot-1"></open-chat-studio-widget>');
    await installProvider(page, provider);
    await send(page);

    expect(provider).toHaveBeenCalledWith({ forceRefresh: false });
    expect(startHeaders()['Authorization']).toBe('Bearer tok-abc');
    expect(page.rootInstance.activeSessionId).toBe('session-1');
  });

  it('sends no Authorization header when no provider is set', async () => {
    const page = await widget('<open-chat-studio-widget chatbot-id="bot-1" embed-key="key-1"></open-chat-studio-widget>');
    await send(page);

    expect(startHeaders()).not.toHaveProperty('Authorization');
    expect(startHeaders()['X-Embed-Key']).toBe('key-1');
  });

  it('keeps the bearer token off the requests that follow the start', async () => {
    const page = await widget('<open-chat-studio-widget chatbot-id="bot-1"></open-chat-studio-widget>');
    await installProvider(page, () => 'tok-abc');
    await send(page);

    const others = fetchMock.mock.calls.filter(([url]) => !String(url).includes('/api/chat/start/'));
    expect(others.length).toBeGreaterThan(0);
    for (const [, init] of others) {
      expect(init.headers).not.toHaveProperty('Authorization');
    }
  });

  it('uses a replaced provider on the next start without losing the live session', async () => {
    const page = await widget('<open-chat-studio-widget chatbot-id="bot-1"></open-chat-studio-widget>');
    await installProvider(page, () => 'tok-old');
    await send(page);

    await installProvider(page, () => 'tok-new');

    // The session survives the swap -- the provider is pushed into the live
    // service rather than the service being rebuilt around it.
    expect(page.rootInstance.activeSessionId).toBe('session-1');

    await page.rootInstance.clearSession();
    await send(page, 'again');

    expect(startHeaders(0)['Authorization']).toBe('Bearer tok-old');
    expect(startHeaders(1)['Authorization']).toBe('Bearer tok-new');
  });

  it('tolerates a provider installed before any session has been started', async () => {
    const page = await widget('<open-chat-studio-widget chatbot-id="bot-1"></open-chat-studio-widget>');
    await installProvider(page, () => 'tok-new');

    await send(page);
    expect(startHeaders()['Authorization']).toBe('Bearer tok-new');
  });

  it('surfaces the server reason when admission is refused', async () => {
    startDenied = true;
    const page = await widget('<open-chat-studio-widget chatbot-id="bot-1"></open-chat-studio-widget>');
    await installProvider(page, () => 'tok-stale');
    await send(page);

    const notice = page.rootInstance.messages.at(-1);
    expect(notice.role).toBe('system');
    expect(notice.content).toContain('Authentication required to chat with this chatbot');
    expect(page.rootInstance.activeSessionId).toBeUndefined();
    expect(page.rootInstance.isLoading).toBe(false);
  });

  it('retries once through the provider when the first token is refused', async () => {
    startDenied = true;
    const provider = jest.fn(async ({ forceRefresh }: { forceRefresh: boolean }) => {
      if (forceRefresh) {
        startDenied = false;
        return 'tok-fresh';
      }
      return 'tok-cached';
    });

    const page = await widget('<open-chat-studio-widget chatbot-id="bot-1"></open-chat-studio-widget>');
    await installProvider(page, provider);
    await send(page);

    expect(provider.mock.calls).toEqual([[{ forceRefresh: false }], [{ forceRefresh: true }]]);
    expect(startHeaders(1)['Authorization']).toBe('Bearer tok-fresh');
    expect(page.rootInstance.activeSessionId).toBe('session-1');
  });

  it('re-crosses the OAuth gate when an aged-out session restarts', async () => {
    // ADR-0054 expires a session token on absolute age; the widget's recovery
    // starts a fresh session, which is the point of the bounded lifetime -- the
    // credential is checked again rather than passed once.
    let sessionExpired = false;
    renewDenied = true;
    jest.spyOn(console, 'warn').mockImplementation(() => undefined);
    fetchMock.mockImplementation((url: string) => {
      if (sessionExpired && !url.includes('/api/chat/start/') && !url.includes('/token/')) {
        return Promise.resolve(jsonResponse(403, { error: 'Session expired', code: 'session_expired' }));
      }
      return Promise.resolve(router(url));
    });

    const provider = jest.fn().mockResolvedValue('tok-1');
    const page = await widget('<open-chat-studio-widget chatbot-id="bot-1"></open-chat-studio-widget>');
    await installProvider(page, provider);
    await send(page);
    expect(page.rootInstance.activeSessionId).toBe('session-1');

    sessionExpired = true;
    await send(page, 'still here?');
    expect(headersOf('/token/')['Authorization']).toBe('Bearer tok-1');
    expect(page.rootInstance.activeSessionId).toBeUndefined();

    // The resend after the expiry notice asks the host for a token again.
    sessionExpired = false;
    provider.mockResolvedValue('tok-2');
    await send(page, 'resent');

    expect(startHeaders(0)['Authorization']).toBe('Bearer tok-1');
    expect(startHeaders(1)['Authorization']).toBe('Bearer tok-2');
    expect(page.rootInstance.activeSessionId).toBe('session-1');
  });

  it('renews the session token through the provider before it expires', async () => {
    tokenLifetimeSeconds = 30;
    const provider = jest.fn().mockResolvedValue('tok-abc');
    const page = await widget('<open-chat-studio-widget chatbot-id="bot-1"></open-chat-studio-widget>');
    await installProvider(page, provider);
    await send(page);

    // The start response's token is inside the renewal lead, so the first
    // session-bound request renews it and carries the replacement.
    expect(headersOf('/token/')['Authorization']).toBe('Bearer tok-abc');
    expect(headersOf('/message/')['X-Session-Token']).toBe('sess-tok-2');
    expect(provider.mock.calls).toEqual([[{ forceRefresh: false }], [{ forceRefresh: false }]]);

    // The conversation continues on the same session, with no expiry notice.
    expect(page.rootInstance.activeSessionId).toBe('session-1');
    expect(page.rootInstance.messages.filter(m => m.role === 'system')).toEqual([]);
  });

  it('persists the renewed token and expiry so a reload resumes with them', async () => {
    tokenLifetimeSeconds = 30;
    const page = await widget('<open-chat-studio-widget chatbot-id="bot-1"></open-chat-studio-widget>');
    await installProvider(page, () => 'tok-abc');
    await send(page);

    expect(storedValue('ocs-chat-token-bot-1')).toBe('sess-tok-2');
    expect(Date.parse(storedValue('ocs-chat-token-expires-bot-1'))).toBeGreaterThan(Date.now() + 3000 * 1000);
    expect(page.rootInstance['currentSessionToken']).toBe('sess-tok-2');
  });

  it('renews and retries an upload the server refuses as expired', async () => {
    // The token does not look due to the widget, so only the server's refusal can trigger renewal.
    const page = await widget('<open-chat-studio-widget chatbot-id="bot-1" allow-attachments="true"></open-chat-studio-widget>');
    await installProvider(page, () => 'tok-abc');
    await send(page);
    page.rootInstance['selectedFiles'] = [{ file: new File(['hello'], 'a.txt', { type: 'text/plain' }) }];
    uploadRefusals = 1;

    await send(page, 'with a file');

    const callsTo = (path: string) => fetchMock.mock.calls.filter(([url]) => String(url).includes(path)).map(([, init]) => init);
    expect(callsTo('/upload/').map(init => init.headers['X-Session-Token'])).toEqual(['sess-tok', 'sess-tok-2']);
    expect(headersOf('/token/')['Authorization']).toBe('Bearer tok-abc');
    expect(JSON.parse(callsTo('/message/')[1].body).attachment_ids).toEqual([42]);
    expect(page.rootInstance.activeSessionId).toBe('session-1');
    expect(page.rootInstance.messages.filter(m => m.role === 'system')).toEqual([]);
  });

  it('does not renew a token that still has time left', async () => {
    const page = await widget('<open-chat-studio-widget chatbot-id="bot-1"></open-chat-studio-widget>');
    await installProvider(page, () => 'tok-abc');
    await send(page);

    expect(headersOf('/token/')).toBeUndefined();
    expect(headersOf('/message/')['X-Session-Token']).toBe('sess-tok');
  });

  it('never writes the auth token to local storage', async () => {
    tokenLifetimeSeconds = 30;
    const page = await widget('<open-chat-studio-widget chatbot-id="bot-1"></open-chat-studio-widget>');
    await installProvider(page, () => 'tok-secret');
    await send(page);

    expect(headersOf('/token/')['Authorization']).toBe('Bearer tok-secret');

    const setItem = window.localStorage.setItem as jest.Mock;
    expect(setItem).toHaveBeenCalled();
    const written = setItem.mock.calls.map(([key, value]) => `${key}=${value}`).join('\n');
    expect(written).not.toContain('tok-secret');
  });
});
