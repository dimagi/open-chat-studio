import { newSpecPage, SpecPage } from '@stencil/core/testing';
import { OcsChat } from './ocs-chat';

const BLOCKED_MESSAGE = 'You have been blocked from using this chatbot.';

describe('ocs-chat blocked participant', () => {
  let fetchMock: jest.Mock;
  /** The body and status `chat/start/` answers with. */
  let startRefusal: { status: number; body: unknown } | undefined;

  function jsonResponse(status: number, body: unknown) {
    return {
      ok: status < 400,
      status,
      statusText: String(status),
      headers: { get: () => null },
      json: () => Promise.resolve(body),
    } as unknown as Response;
  }

  function router(url: string) {
    if (url.includes('/api/chat/start/')) {
      if (startRefusal) {
        return jsonResponse(startRefusal.status, startRefusal.body);
      }
      return jsonResponse(201, { session_id: 'session-1', session_token: null, expires_at: null, chatbot: {}, participant: {} });
    }
    if (url.includes('/message/')) {
      return jsonResponse(200, { task_id: 'task-1', status: 'processing' });
    }
    return jsonResponse(200, { messages: [], has_more: false, session_status: 'active' });
  }

  function startCalls(): number {
    return fetchMock.mock.calls.filter(([url]) => String(url).includes('/api/chat/start/')).length;
  }

  function newChatButton(page: SpecPage): Element | null {
    return page.root.shadowRoot.querySelector('.header-buttons button[aria-label="Start new chat"]');
  }

  async function widget() {
    const page = await newSpecPage({ components: [OcsChat], html: '<open-chat-studio-widget chatbot-id="bot-1" visible="true"></open-chat-studio-widget>' });
    await page.waitForChanges();
    return page;
  }

  async function send(page: SpecPage, message = 'hello') {
    await page.rootInstance.sendMessage(message);
    await page.waitForChanges();
  }

  beforeEach(() => {
    startRefusal = undefined;
    fetchMock = jest.fn((url: string) => Promise.resolve(router(url)));
    global.fetch = fetchMock as unknown as typeof fetch;
    Object.defineProperty(window, 'localStorage', {
      value: { getItem: jest.fn(() => null), setItem: jest.fn(), removeItem: jest.fn(), clear: jest.fn() },
      writable: true,
    });
    Object.defineProperty(window, 'crypto', {
      value: { getRandomValues: (arr: Uint8Array) => arr.fill(7) },
      writable: true,
    });
  });

  describe('when the start is refused with participant_blocked', () => {
    beforeEach(() => {
      startRefusal = { status: 403, body: { code: 'participant_blocked', detail: BLOCKED_MESSAGE } };
    });

    it('shows the server detail without asking the participant to try again', async () => {
      const page = await widget();
      await send(page);

      const notice = page.rootInstance.messages.at(-1);
      expect(notice.role).toBe('system');
      expect(notice.content).toBe(BLOCKED_MESSAGE);
      expect(notice.content).not.toContain('Failed to start chat session');
      expect(notice.content).not.toContain('try again');
      expect(page.rootInstance.activeSessionId).toBeUndefined();
    });

    it('locks the composer and does not start another session on a later send', async () => {
      const page = await widget();
      await send(page);
      await send(page, 'again');

      expect(startCalls()).toBe(1);
      expect(page.root.shadowRoot.querySelector('textarea').hasAttribute('disabled')).toBe(true);
    });

    it('does not offer the new-chat button', async () => {
      const page = await widget();
      await send(page);

      expect(newChatButton(page)).toBeNull();
    });
  });

  it('keeps the generic start failure for another 403', async () => {
    startRefusal = { status: 403, body: { detail: 'You do not have permission to perform this action.' } };
    const page = await widget();
    await send(page);

    const notice = page.rootInstance.messages.at(-1);
    expect(notice.role).toBe('system');
    expect(notice.content).toContain('Failed to start chat session');
    expect(notice.content).toContain('Please try again.');
    expect(page.root.shadowRoot.querySelector('textarea').hasAttribute('disabled')).toBe(false);
    expect(newChatButton(page)).not.toBeNull();
  });
});
