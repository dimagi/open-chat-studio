import { newSpecPage, SpecPage } from '@stencil/core/testing';
import { OcsChat } from './ocs-chat';
import { ParticipantBlockedError, SessionAccessError } from '../../services/chat-session-service';
import { installWebCrypto, stubChatService } from './ocs-chat.test-helpers';

const BLOCKED_MESSAGE = 'You have been blocked from using this chatbot.';
const SESSION_KEY = 'ocs-chat-session-bot-1';
const MESSAGES_KEY = 'ocs-chat-messages-bot-1';
const RESTORED_MESSAGE = { created_at: '2026-01-01T00:00:00Z', role: 'assistant', content: 'Hello again', attachments: [] };

describe('ocs-chat blocked participant', () => {
  let store: Record<string, string>;
  let startSession: jest.Mock;

  function newChatButton(page: SpecPage): Element | null {
    return page.root.shadowRoot.querySelector('.header-buttons button[aria-label="Start new chat"]');
  }

  function composer(page: SpecPage): HTMLTextAreaElement {
    return page.root.shadowRoot.querySelector('textarea');
  }

  async function mountWidget(attrs = ''): Promise<SpecPage> {
    const page = await newSpecPage({ components: [OcsChat], html: `<open-chat-studio-widget chatbot-id="bot-1" visible="true" ${attrs}></open-chat-studio-widget>` });
    stubChatService(page, { startMessagePolling: jest.fn(() => ({ stop: jest.fn() })) });
    await new Promise(resolve => setTimeout(resolve, 0));
    await page.waitForChanges();
    return page;
  }

  async function widgetAfterRefusedStart(refusal: Error): Promise<SpecPage> {
    const page = await mountWidget();
    startSession = jest.fn().mockRejectedValue(refusal);
    stubChatService(page, { startSession });
    await send(page, 'hello');
    return page;
  }

  async function restoredWidget(attrs = ''): Promise<SpecPage> {
    store[SESSION_KEY] = 's-restored';
    store[MESSAGES_KEY] = JSON.stringify([RESTORED_MESSAGE]);
    const page = await mountWidget(attrs);
    startSession = jest.fn();
    stubChatService(page, { startSession });
    return page;
  }

  async function send(page: SpecPage, message: string) {
    await page.rootInstance.sendMessage(message);
    await page.waitForChanges();
  }

  beforeEach(() => {
    installWebCrypto();
    store = {};
    Object.defineProperty(window, 'localStorage', {
      value: {
        getItem: jest.fn((key: string) => (key in store ? store[key] : null)),
        setItem: jest.fn((key: string, value: string) => {
          store[key] = value;
        }),
        removeItem: jest.fn((key: string) => {
          delete store[key];
        }),
        clear: jest.fn(),
      },
      writable: true,
    });
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  function expectBlocked(page: SpecPage) {
    const notice = page.rootInstance.messages.at(-1);
    expect(notice.role).toBe('system');
    expect(notice.content).toBe(BLOCKED_MESSAGE);
    expect(page.rootInstance.activeSessionId).toBeUndefined();
    expect(composer(page).hasAttribute('disabled')).toBe(true);
    expect(newChatButton(page)).toBeNull();
    expect(store[SESSION_KEY]).toBeUndefined();
    expect(store[MESSAGES_KEY]).toBeUndefined();
  }

  describe('when the start is refused with participant_blocked', () => {
    let page: SpecPage;

    beforeEach(async () => {
      page = await widgetAfterRefusedStart(new ParticipantBlockedError(BLOCKED_MESSAGE));
    });

    it('shows the server detail, locks the composer and keeps nothing in storage', () => {
      expectBlocked(page);
    });

    it('shows the blocked placeholder in the composer', () => {
      expect(composer(page).getAttribute('placeholder')).toBe('You can no longer send messages to this chatbot.');
    });

    it('does not start another session on a later send', async () => {
      await send(page, 'again');

      expect(startSession).toHaveBeenCalledTimes(1);
    });
  });

  it('shows the translated blocked notice when the refusal carries no detail', async () => {
    const page = await widgetAfterRefusedStart(new ParticipantBlockedError());

    expect(page.rootInstance.messages.at(-1).content).toBe('You can no longer send messages to this chatbot.');
  });

  it('renders the blocked notice as plain text', async () => {
    const notice = '**Blocked** <em>now</em>';
    const page = await widgetAfterRefusedStart(new ParticipantBlockedError(notice));

    const bubble = page.root.shadowRoot.querySelector('.message-bubble-system');
    expect(bubble.querySelector('.chat-plain-text').textContent).toBe(notice);
    expect(bubble.querySelector('strong, em')).toBeNull();
  });

  describe('when a restored session is refused with participant_blocked', () => {
    it('on send, shows the server detail, locks the widget and clears the stored session', async () => {
      const page = await restoredWidget();
      const sendMessage = jest.fn().mockRejectedValue(new ParticipantBlockedError(BLOCKED_MESSAGE));
      stubChatService(page, { sendMessage });

      await send(page, 'hello');

      expectBlocked(page);
      expect(page.rootInstance.messages.map(m => m.content)).toEqual([RESTORED_MESSAGE.content, BLOCKED_MESSAGE]);
      await send(page, 'again');
      expect(sendMessage).toHaveBeenCalledTimes(1);
      expect(startSession).not.toHaveBeenCalled();
    });

    it('on upload, shows the server detail, locks the widget and clears the stored session', async () => {
      const page = await restoredWidget('allow-attachments="true"');
      const sendMessage = jest.fn();
      stubChatService(page, { sendMessage });
      page.rootInstance.selectedFiles = [{ file: new File(['hello'], 'a.txt', { type: 'text/plain' }) }];
      jest.spyOn(page.rootInstance['attachmentManager'], 'uploadPendingFiles').mockResolvedValue({
        selectedFiles: [{ file: new File(['hello'], 'a.txt', { type: 'text/plain' }) }],
        uploadedIds: [],
        tokenRejected: false,
        participantBlocked: { detail: BLOCKED_MESSAGE },
      });

      await send(page, 'hello');

      expectBlocked(page);
      expect(sendMessage).not.toHaveBeenCalled();
      expect(page.rootInstance.selectedFiles).toEqual([]);
    });
  });

  it('keeps the generic send failure and the session for another error', async () => {
    const page = await restoredWidget();
    stubChatService(page, { sendMessage: jest.fn().mockRejectedValue(new Error('Something went wrong.')) });

    await send(page, 'hello');

    const notice = page.rootInstance.messages.at(-1);
    expect(notice.content).toContain('Something went wrong.');
    expect(notice.content).toContain('Please try again.');
    expect(page.rootInstance.activeSessionId).toBe('s-restored');
    expect(composer(page).hasAttribute('disabled')).toBe(false);
  });

  it('keeps the generic start failure for another 403', async () => {
    const page = await widgetAfterRefusedStart(new SessionAccessError(403, 'permission_denied', 'Forbidden'));

    const notice = page.rootInstance.messages.at(-1);
    expect(notice.role).toBe('system');
    expect(notice.content).toContain('Failed to start chat session');
    expect(notice.content).toContain('Please try again.');
    expect(page.root.shadowRoot.querySelector('.message-bubble-system .chat-markdown strong')).not.toBeNull();
    expect(composer(page).hasAttribute('disabled')).toBe(false);
    expect(newChatButton(page)).not.toBeNull();
  });
});
