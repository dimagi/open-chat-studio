import { newSpecPage, SpecPage } from '@stencil/core/testing';
import { OcsChat } from './ocs-chat';
import { ParticipantBlockedError, SessionAccessError } from '../../services/chat-session-service';
import { installWebCrypto, stubChatService } from './ocs-chat.test-helpers';

const BLOCKED_MESSAGE = 'You have been blocked from using this chatbot.';

describe('ocs-chat blocked participant', () => {
  let startSession: jest.Mock;

  function newChatButton(page: SpecPage): Element | null {
    return page.root.shadowRoot.querySelector('.header-buttons button[aria-label="Start new chat"]');
  }

  function composer(page: SpecPage): HTMLTextAreaElement {
    return page.root.shadowRoot.querySelector('textarea');
  }

  async function widgetAfterRefusedStart(refusal: Error): Promise<SpecPage> {
    const page = await newSpecPage({ components: [OcsChat], html: '<open-chat-studio-widget chatbot-id="bot-1" visible="true"></open-chat-studio-widget>' });
    startSession = jest.fn().mockRejectedValue(refusal);
    stubChatService(page, { startSession });
    await page.waitForChanges();
    await send(page, 'hello');
    return page;
  }

  async function send(page: SpecPage, message: string) {
    await page.rootInstance.sendMessage(message);
    await page.waitForChanges();
  }

  beforeEach(() => {
    installWebCrypto();
    Object.defineProperty(window, 'localStorage', {
      value: { getItem: jest.fn(() => null), setItem: jest.fn(), removeItem: jest.fn(), clear: jest.fn() },
      writable: true,
    });
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  describe('when the start is refused with participant_blocked', () => {
    let page: SpecPage;

    beforeEach(async () => {
      page = await widgetAfterRefusedStart(new ParticipantBlockedError(BLOCKED_MESSAGE));
    });

    it('shows the server detail without asking the participant to try again', () => {
      const notice = page.rootInstance.messages.at(-1);
      expect(notice.role).toBe('system');
      expect(notice.content).toBe(BLOCKED_MESSAGE);
      expect(page.rootInstance.activeSessionId).toBeUndefined();
    });

    it('locks the composer and does not start another session on a later send', async () => {
      await send(page, 'again');

      expect(startSession).toHaveBeenCalledTimes(1);
      expect(composer(page).hasAttribute('disabled')).toBe(true);
    });

    it('does not offer the new-chat button', () => {
      expect(newChatButton(page)).toBeNull();
    });
  });

  it('keeps the generic start failure for another 403', async () => {
    const page = await widgetAfterRefusedStart(new SessionAccessError(403, 'permission_denied', 'Forbidden'));

    const notice = page.rootInstance.messages.at(-1);
    expect(notice.role).toBe('system');
    expect(notice.content).toContain('Failed to start chat session');
    expect(notice.content).toContain('Please try again.');
    expect(composer(page).hasAttribute('disabled')).toBe(false);
    expect(newChatButton(page)).not.toBeNull();
  });
});
