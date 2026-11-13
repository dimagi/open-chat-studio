import { newSpecPage } from '@stencil/core/testing';
import { OcsChat } from './ocs-chat';
import { installWebCrypto, setupFetchMock, stubChatService } from './ocs-chat.test-helpers';

const mockStartSession = jest.fn();
const mockSendMessage = jest.fn();
const mockPollTask = jest.fn();
const mockStartMessagePolling = jest.fn();
const mockFetchAllMessages = jest.fn();

describe('ocs-chat kiosk restart after session end', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    installWebCrypto();
    mockStartSession.mockResolvedValue({ session_id: 'kiosk-session' });
    mockSendMessage.mockResolvedValue({ status: 'success', task_id: 'task' });
    mockPollTask.mockReturnValue({ cancel: jest.fn() });
    mockStartMessagePolling.mockReturnValue({ stop: jest.fn() });
    const store: Record<string, string> = {};
    Object.defineProperty(window, 'localStorage', {
      value: {
        getItem: jest.fn((k: string) => store[k] ?? null),
        setItem: jest.fn((k: string, v: string) => {
          store[k] = v;
        }),
        removeItem: jest.fn((k: string) => {
          delete store[k];
        }),
        clear: jest.fn(),
      },
      writable: true,
    });
    global.fetch = setupFetchMock('kiosk-session');
  });

  async function endedPage(attrs: string) {
    const page = await newSpecPage({
      components: [OcsChat],
      html: `<open-chat-studio-widget chatbot-id="test-bot" visible="true" ${attrs}></open-chat-studio-widget>`,
    });
    stubChatService(page, {
      startSession: mockStartSession,
      sendMessage: mockSendMessage,
      startMessagePolling: mockStartMessagePolling,
      pollTask: mockPollTask,
    });
    await page.waitForChanges();
    await page.rootInstance.sendMessage('hello');
    await page.waitForChanges();
    const callbacks = mockStartMessagePolling.mock.calls[0][1];
    callbacks.onSessionEnded();
    await page.waitForChanges();
    return page;
  }

  it('shows a restart button in kiosk mode once the session has ended', async () => {
    const page = await endedPage('mode="kiosk"');
    const button = page.root?.shadowRoot?.querySelector('.kiosk-restart') as HTMLButtonElement;
    expect(button).not.toBeNull();
    expect(button.textContent).toBe('Start new chat');
  });

  it('does not show the restart button in a read-only (disabled) kiosk widget', async () => {
    const page = await endedPage('mode="kiosk"');
    page.rootInstance.disabled = true;
    await page.waitForChanges();
    expect(page.root?.shadowRoot?.querySelector('.kiosk-restart')).toBeNull();
  });

  it('does not show the restart button before the session has ended', async () => {
    const page = await newSpecPage({
      components: [OcsChat],
      html: '<open-chat-studio-widget chatbot-id="test-bot" mode="kiosk"></open-chat-studio-widget>',
    });
    await page.waitForChanges();
    expect(page.root?.shadowRoot?.querySelector('.kiosk-restart')).toBeNull();
  });

  it('does not show the restart button in standard mode (the header button covers it)', async () => {
    const page = await endedPage('');
    expect(page.root?.shadowRoot?.querySelector('.kiosk-restart')).toBeNull();
  });

  it('does not show the restart button for a bound kiosk session', async () => {
    mockFetchAllMessages.mockResolvedValue([]);
    const page = await newSpecPage({
      components: [OcsChat],
      html: '<open-chat-studio-widget chatbot-id="test-bot" mode="kiosk" session-id="server-session"></open-chat-studio-widget>',
    });
    stubChatService(page, { fetchAllMessages: mockFetchAllMessages, startMessagePolling: mockStartMessagePolling });
    await page.waitForChanges();
    await new Promise(resolve => setTimeout(resolve, 0));
    await page.waitForChanges();
    mockStartMessagePolling.mock.calls[0][1].onSessionEnded();
    await page.waitForChanges();

    expect(page.rootInstance.sessionEnded).toBe(true);
    expect(page.root?.shadowRoot?.querySelector('.kiosk-restart')).toBeNull();
  });

  it('clicking restart clears the ended session so the next message starts a new one', async () => {
    const page = await endedPage('mode="kiosk"');
    const button = page.root?.shadowRoot?.querySelector('.kiosk-restart') as HTMLButtonElement;

    button.click();
    await new Promise(resolve => setTimeout(resolve, 0));
    await page.waitForChanges();

    expect(page.rootInstance.sessionEnded).toBe(false);
    expect(page.rootInstance.activeSessionId).toBeUndefined();
    expect(page.rootInstance.messages).toEqual([]);
    expect(window.localStorage.removeItem).toHaveBeenCalledWith('ocs-chat-session-test-bot');
    expect(page.root?.shadowRoot?.querySelector('.kiosk-restart')).toBeNull();
    const textarea = page.root?.shadowRoot?.querySelector('.message-textarea') as HTMLTextAreaElement;
    expect(textarea.hasAttribute('disabled')).toBe(false);

    mockStartSession.mockClear();
    await page.rootInstance.sendMessage('again');
    expect(mockStartSession).toHaveBeenCalledTimes(1);
  });

  async function restartedPage() {
    const page = await endedPage('mode="kiosk"');
    const messageCallbacks = mockStartMessagePolling.mock.calls[0][1];
    const taskCallbacks = mockPollTask.mock.calls[0][2];
    const button = page.root?.shadowRoot?.querySelector('.kiosk-restart') as HTMLButtonElement;
    button.click();
    await new Promise(resolve => setTimeout(resolve, 0));
    await page.waitForChanges();
    (window.localStorage.setItem as jest.Mock).mockClear();
    return { page, messageCallbacks, taskCallbacks };
  }

  it('ignores message-poll callbacks from the session that was cleared by the restart', async () => {
    const { page, messageCallbacks } = await restartedPage();

    messageCallbacks.onMessages([{ created_at: '2026-01-01T00:00:00Z', role: 'assistant', content: 'stale reply', attachments: [] }]);
    messageCallbacks.onSessionEnded();
    await page.waitForChanges();

    expect(page.rootInstance.messages).toEqual([]);
    expect(page.rootInstance.sessionEnded).toBe(false);
    expect(window.localStorage.setItem).not.toHaveBeenCalledWith('ocs-chat-messages-test-bot', expect.anything());
  });

  it('ignores task-poll callbacks from the session that was cleared by the restart', async () => {
    const { page, taskCallbacks } = await restartedPage();

    taskCallbacks.onMessage({ created_at: '2026-01-01T00:00:00Z', role: 'assistant', content: 'stale reply', attachments: [] });
    await page.waitForChanges();

    expect(page.rootInstance.messages).toEqual([]);
    expect(page.rootInstance.isTyping).toBe(false);
    expect(window.localStorage.setItem).not.toHaveBeenCalledWith('ocs-chat-messages-test-bot', expect.anything());
  });

  describe('kiosk new-chat button', () => {
    const KIOSK_WITH_NEW_CHAT = 'mode="kiosk" show-new-chat-button="true"';

    async function pageWithMessage(attrs: string) {
      const page = await newSpecPage({
        components: [OcsChat],
        html: `<open-chat-studio-widget chatbot-id="test-bot" ${attrs}></open-chat-studio-widget>`,
      });
      stubChatService(page, {
        startSession: mockStartSession,
        sendMessage: mockSendMessage,
        startMessagePolling: mockStartMessagePolling,
        pollTask: mockPollTask,
      });
      await page.waitForChanges();
      await page.rootInstance.sendMessage('hello');
      await page.waitForChanges();
      return page;
    }

    const newChatButton = (page: Awaited<ReturnType<typeof newSpecPage>>) => page.root?.shadowRoot?.querySelector('.kiosk-new-chat') as HTMLButtonElement | null;

    it('shows the button once there are messages when show-new-chat-button is set', async () => {
      const page = await pageWithMessage(KIOSK_WITH_NEW_CHAT);
      const button = newChatButton(page);
      expect(button).not.toBeNull();
      expect(button.getAttribute('aria-label')).toBe('Start new chat');
    });

    it('does not show the button before any message is sent', async () => {
      const page = await newSpecPage({
        components: [OcsChat],
        html: '<open-chat-studio-widget chatbot-id="test-bot" mode="kiosk" show-new-chat-button="true"></open-chat-studio-widget>',
      });
      await page.waitForChanges();
      expect(newChatButton(page)).toBeNull();
    });

    it.each([
      ['the prop is not set', 'mode="kiosk"'],
      ['the widget is in standard mode', 'visible="true" show-new-chat-button="true"'],
    ])('does not show the button when %s', async (_label, attrs) => {
      const page = await pageWithMessage(attrs);
      expect(newChatButton(page)).toBeNull();
    });

    it('does not show the button in a read-only (disabled) widget', async () => {
      const page = await pageWithMessage(KIOSK_WITH_NEW_CHAT);
      page.rootInstance.disabled = true;
      await page.waitForChanges();
      expect(newChatButton(page)).toBeNull();
    });

    it('is replaced by the restart button once the session ends', async () => {
      const page = await pageWithMessage(KIOSK_WITH_NEW_CHAT);
      mockStartMessagePolling.mock.calls[0][1].onSessionEnded();
      await page.waitForChanges();
      expect(newChatButton(page)).toBeNull();
      expect(page.root?.shadowRoot?.querySelector('.kiosk-restart')).not.toBeNull();
    });

    it('asks for confirmation inside the chat area before clearing the session', async () => {
      const page = await pageWithMessage(KIOSK_WITH_NEW_CHAT);
      newChatButton(page).click();
      await page.waitForChanges();

      const overlay = page.root?.shadowRoot?.querySelector('.confirmation-overlay');
      expect(overlay).not.toBeNull();
      expect(overlay.classList.contains('confirmation-overlay-kiosk')).toBe(true);
      expect(page.rootInstance.messages.length).toBeGreaterThan(0);

      (page.root?.shadowRoot?.querySelector('.confirmation-button-confirm') as HTMLButtonElement).click();
      await new Promise(resolve => setTimeout(resolve, 0));
      await page.waitForChanges();

      expect(page.rootInstance.messages).toEqual([]);
      expect(page.rootInstance.activeSessionId).toBeUndefined();
      expect(page.root?.shadowRoot?.querySelector('.confirmation-overlay')).toBeNull();
      expect(newChatButton(page)).toBeNull();
    });

    it('drops an upload that finishes after a new chat was confirmed', async () => {
      const page = await pageWithMessage(`${KIOSK_WITH_NEW_CHAT} allow-attachments="true"`);
      const file = new File(['hello'], 'a.txt', { type: 'text/plain' });
      let finishUpload: (result: unknown) => void;
      jest.spyOn(page.rootInstance['attachmentManager'], 'uploadPendingFiles').mockReturnValue(
        new Promise(resolve => {
          finishUpload = resolve;
        }),
      );
      page.rootInstance.selectedFiles = [{ file }];
      mockSendMessage.mockClear();

      const sending = page.rootInstance.sendMessage('with a file');
      await page.waitForChanges();
      // The kiosk button is disabled during the upload; the standard header button is not.
      await page.rootInstance.confirmNewChat();
      await page.waitForChanges();

      finishUpload({ selectedFiles: [{ file, uploaded: true }], uploadedIds: [7] });
      await sending;
      await page.waitForChanges();

      expect(mockSendMessage).not.toHaveBeenCalled();
      expect(page.rootInstance.messages).toEqual([]);
      expect(page.rootInstance.selectedFiles).toEqual([]);
      expect(page.rootInstance.activeSessionId).toBeUndefined();
    });

    it.each([
      ['uploading files', 'isUploadingFiles'],
      ['starting a session', 'isLoading'],
    ])('is disabled while %s', async (_label, flag) => {
      const page = await pageWithMessage(KIOSK_WITH_NEW_CHAT);
      page.rootInstance[flag] = true;
      await page.waitForChanges();
      expect(newChatButton(page).hasAttribute('disabled')).toBe(true);
    });

    it('stays enabled while waiting for the reply', async () => {
      const page = await pageWithMessage(KIOSK_WITH_NEW_CHAT);
      page.rootInstance.isTyping = true;
      await page.waitForChanges();
      expect(newChatButton(page).hasAttribute('disabled')).toBe(false);
    });

    it('keeps the session when the confirmation is cancelled', async () => {
      const page = await pageWithMessage(KIOSK_WITH_NEW_CHAT);
      newChatButton(page).click();
      await page.waitForChanges();
      (page.root?.shadowRoot?.querySelector('.confirmation-button-cancel') as HTMLButtonElement).click();
      await page.waitForChanges();

      expect(page.root?.shadowRoot?.querySelector('.confirmation-overlay')).toBeNull();
      expect(page.rootInstance.activeSessionId).toBe('kiosk-session');
    });
  });
});
