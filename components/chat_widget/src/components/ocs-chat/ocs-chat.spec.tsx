import { newSpecPage } from '@stencil/core/testing';
import { OcsChat } from './ocs-chat';
import { TranslationManager } from '../../utils/translations';

describe('ocs-chat', () => {
  describe('Welcome Messages Display', () => {
    it('should display welcome messages when provided via translation files', async () => {
      const page = await newSpecPage({
        components: [OcsChat],
        html: `<open-chat-studio-widget chatbot-id="test-bot" visible="true"></open-chat-studio-widget>`,
      });

      const component = page.rootInstance as OcsChat;

      // Mock translation manager to return welcome messages
      component.translationManager = new TranslationManager('en', {
        'content.welcomeMessages': ['Hello from translations!', 'Welcome to our chat.'],
      });

      // Ensure no messages exist yet
      component.messages = [];
      component.activeSessionId = 'test-session';

      await page.waitForChanges();

      // Check that welcome messages container is rendered
      const welcomeMessages = page.root?.shadowRoot?.querySelector('.welcome-messages');
      expect(welcomeMessages).toBeTruthy();

      // Check that both messages are displayed
      const messageBubbles = welcomeMessages?.querySelectorAll('.message-bubble-assistant');
      expect(messageBubbles?.length).toBe(2);
    });

    it('should display welcome messages from widget attributes when no translations provided', async () => {
      const page = await newSpecPage({
        components: [OcsChat],
        html: `<open-chat-studio-widget
          chatbot-id="test-bot"
          visible="true"
          welcome-messages='["Hello from attributes!", "Welcome!"]'
        ></open-chat-studio-widget>`,
      });

      const component = page.rootInstance as OcsChat;
      component.messages = [];
      component.activeSessionId = 'test-session';

      await page.waitForChanges();

      const welcomeMessages = page.root?.shadowRoot?.querySelector('.welcome-messages');
      expect(welcomeMessages).toBeTruthy();

      const messageBubbles = welcomeMessages?.querySelectorAll('.message-bubble-assistant');
      expect(messageBubbles?.length).toBe(2);
    });

    it('should prioritize translation file messages over widget attributes', async () => {
      const page = await newSpecPage({
        components: [OcsChat],
        html: `<open-chat-studio-widget
          chatbot-id="test-bot"
          visible="true"
          welcome-messages='["Attribute message"]'
        ></open-chat-studio-widget>`,
      });

      const component = page.rootInstance as OcsChat;

      // Translation messages should override attribute messages
      component.translationManager = new TranslationManager('en', {
        'content.welcomeMessages': ['Translation message 1', 'Translation message 2'],
      });

      component.messages = [];
      component.activeSessionId = 'test-session';

      await page.waitForChanges();

      const welcomeMessages = page.root?.shadowRoot?.querySelector('.welcome-messages');
      const messageBubbles = welcomeMessages?.querySelectorAll('.message-bubble-assistant');

      // Should have 2 messages from translations, not 1 from attributes
      expect(messageBubbles?.length).toBe(2);
    });

    it('should not display welcome messages when chat has messages', async () => {
      const page = await newSpecPage({
        components: [OcsChat],
        html: `<open-chat-studio-widget chatbot-id="test-bot" visible="true"></open-chat-studio-widget>`,
      });

      const component = page.rootInstance as OcsChat;

      component.translationManager = new TranslationManager('en', {
        'content.welcomeMessages': ['Hello from translations!'],
      });

      // Add a message to the chat
      component.messages = [
        {
          created_at: new Date().toISOString(),
          role: 'user',
          content: 'Hello',
          attachments: [],
        },
      ];
      component.activeSessionId = 'test-session';

      await page.waitForChanges();

      // Welcome messages should not be displayed
      const welcomeMessages = page.root?.shadowRoot?.querySelector('.welcome-messages');
      expect(welcomeMessages).toBeFalsy();
    });
  });

  describe('Starter Questions Display', () => {
    it('should display starter questions when provided via translation files', async () => {
      const page = await newSpecPage({
        components: [OcsChat],
        html: `<open-chat-studio-widget chatbot-id="test-bot" visible="true"></open-chat-studio-widget>`,
      });

      const component = page.rootInstance as OcsChat;

      // Mock translation manager to return starter questions
      component.translationManager = new TranslationManager('en', {
        'content.starterQuestions': ['What can you help me with?', 'How does this work?'],
      });

      component.messages = [];
      component.activeSessionId = 'test-session';

      await page.waitForChanges();

      // Check that starter questions container is rendered
      const starterQuestions = page.root?.shadowRoot?.querySelector('.starter-questions');
      expect(starterQuestions).toBeTruthy();

      // Check that both questions are displayed
      const questionButtons = starterQuestions?.querySelectorAll('.starter-question');
      expect(questionButtons?.length).toBe(2);
    });

    it('should display starter questions from widget attributes when no translations provided', async () => {
      const page = await newSpecPage({
        components: [OcsChat],
        html: `<open-chat-studio-widget
          chatbot-id="test-bot"
          visible="true"
          starter-questions='["Question 1?", "Question 2?"]'
        ></open-chat-studio-widget>`,
      });

      const component = page.rootInstance as OcsChat;
      component.messages = [];
      component.activeSessionId = 'test-session';

      await page.waitForChanges();

      const starterQuestions = page.root?.shadowRoot?.querySelector('.starter-questions');
      expect(starterQuestions).toBeTruthy();

      const questionButtons = starterQuestions?.querySelectorAll('.starter-question');
      expect(questionButtons?.length).toBe(2);
    });

    it('should prioritize translation file questions over widget attributes', async () => {
      const page = await newSpecPage({
        components: [OcsChat],
        html: `<open-chat-studio-widget
          chatbot-id="test-bot"
          visible="true"
          starter-questions='["Attribute question"]'
        ></open-chat-studio-widget>`,
      });

      const component = page.rootInstance as OcsChat;

      // Translation questions should override attribute questions
      component.translationManager = new TranslationManager('en', {
        'content.starterQuestions': ['Translation question 1?', 'Translation question 2?', 'Translation question 3?'],
      });

      component.messages = [];
      component.activeSessionId = 'test-session';

      await page.waitForChanges();

      const starterQuestions = page.root?.shadowRoot?.querySelector('.starter-questions');
      const questionButtons = starterQuestions?.querySelectorAll('.starter-question');

      // Should have 3 questions from translations, not 1 from attributes
      expect(questionButtons?.length).toBe(3);
    });

    it('should not display starter questions when chat has messages', async () => {
      const page = await newSpecPage({
        components: [OcsChat],
        html: `<open-chat-studio-widget chatbot-id="test-bot" visible="true"></open-chat-studio-widget>`,
      });

      const component = page.rootInstance as OcsChat;

      component.translationManager = new TranslationManager('en', {
        'content.starterQuestions': ['Question 1?', 'Question 2?'],
      });

      // Add a message to the chat
      component.messages = [
        {
          created_at: new Date().toISOString(),
          role: 'user',
          content: 'Hello',
          attachments: [],
        },
      ];
      component.activeSessionId = 'test-session';

      await page.waitForChanges();

      // Starter questions should not be displayed
      const starterQuestions = page.root?.shadowRoot?.querySelector('.starter-questions');
      expect(starterQuestions).toBeFalsy();
    });
  });

  describe('showButton prop', () => {
    it('should render the button by default', async () => {
      const page = await newSpecPage({
        components: [OcsChat],
        html: `<open-chat-studio-widget chatbot-id="test-bot"></open-chat-studio-widget>`,
      });

      const button = page.root?.shadowRoot?.querySelector('button');
      expect(button).toBeTruthy();
    });

    it('should not render the button when show-button is false', async () => {
      const page = await newSpecPage({
        components: [OcsChat],
        html: `<open-chat-studio-widget chatbot-id="test-bot" show-button="false"></open-chat-studio-widget>`,
      });

      const launcherButton = page.root?.shadowRoot?.querySelector('.chat-btn-icon, .chat-btn-text');
      expect(launcherButton).toBeFalsy();
    });

    it('should still show the chat window when show-button is false and visible is true', async () => {
      const page = await newSpecPage({
        components: [OcsChat],
        html: `<open-chat-studio-widget chatbot-id="test-bot" show-button="false" visible="true"></open-chat-studio-widget>`,
      });

      const component = page.rootInstance as OcsChat;
      component.activeSessionId = 'test-session';
      await page.waitForChanges();

      const chatWindow = page.root?.shadowRoot?.querySelector('#ocs-chat-window');
      expect(chatWindow).toBeTruthy();

      // Header should still be present (showButton only hides the button, not the header)
      const header = page.root?.shadowRoot?.querySelector('.chat-header');
      expect(header).toBeTruthy();
    });
  });

  describe('mode prop', () => {
    describe('kiosk mode', () => {
      it('should be visible by default in kiosk mode', async () => {
        const page = await newSpecPage({
          components: [OcsChat],
          html: `<open-chat-studio-widget chatbot-id="test-bot" mode="kiosk"></open-chat-studio-widget>`,
        });

        const component = page.rootInstance as OcsChat;
        expect(component.visible).toBe(true);
      });

      it('should not render the launcher button in kiosk mode', async () => {
        const page = await newSpecPage({
          components: [OcsChat],
          html: `<open-chat-studio-widget chatbot-id="test-bot" mode="kiosk"></open-chat-studio-widget>`,
        });

        const launcherButton = page.root?.shadowRoot?.querySelector('.chat-btn-icon, .chat-btn-text');
        expect(launcherButton).toBeFalsy();
      });

      it('should not render the header in kiosk mode', async () => {
        const page = await newSpecPage({
          components: [OcsChat],
          html: `<open-chat-studio-widget chatbot-id="test-bot" mode="kiosk"></open-chat-studio-widget>`,
        });

        const component = page.rootInstance as OcsChat;
        component.activeSessionId = 'test-session';
        await page.waitForChanges();

        const header = page.root?.shadowRoot?.querySelector('.chat-header');
        expect(header).toBeFalsy();
      });

      it('should render the chat window with kiosk class', async () => {
        const page = await newSpecPage({
          components: [OcsChat],
          html: `<open-chat-studio-widget chatbot-id="test-bot" mode="kiosk"></open-chat-studio-widget>`,
        });

        const component = page.rootInstance as OcsChat;
        component.activeSessionId = 'test-session';
        await page.waitForChanges();

        const chatWindow = page.root?.shadowRoot?.querySelector('#ocs-chat-window');
        expect(chatWindow).toBeTruthy();
        expect(chatWindow?.classList.contains('chat-window-kiosk')).toBe(true);
      });

      it('should prevent setting visible to false in kiosk mode', async () => {
        const page = await newSpecPage({
          components: [OcsChat],
          html: `<open-chat-studio-widget chatbot-id="test-bot" mode="kiosk"></open-chat-studio-widget>`,
        });

        const component = page.rootInstance as OcsChat;
        component.visible = false;
        await page.waitForChanges();

        expect(component.visible).toBe(true);
      });

      it('should still render chat content (input area, messages) in kiosk mode', async () => {
        const page = await newSpecPage({
          components: [OcsChat],
          html: `<open-chat-studio-widget chatbot-id="test-bot" mode="kiosk"></open-chat-studio-widget>`,
        });

        const component = page.rootInstance as OcsChat;
        component.activeSessionId = 'test-session';
        await page.waitForChanges();

        const chatContent = page.root?.shadowRoot?.querySelector('.chat-content');
        expect(chatContent).toBeTruthy();

        const inputArea = page.root?.shadowRoot?.querySelector('.input-area');
        expect(inputArea).toBeTruthy();
      });
    });

    describe('standard mode (default)', () => {
      it('should behave normally without mode prop', async () => {
        const page = await newSpecPage({
          components: [OcsChat],
          html: `<open-chat-studio-widget chatbot-id="test-bot" visible="true"></open-chat-studio-widget>`,
        });

        const component = page.rootInstance as OcsChat;
        component.activeSessionId = 'test-session';
        await page.waitForChanges();

        // Header should be present
        const header = page.root?.shadowRoot?.querySelector('.chat-header');
        expect(header).toBeTruthy();

        // Window should use normal class
        const chatWindow = page.root?.shadowRoot?.querySelector('#ocs-chat-window');
        expect(chatWindow?.classList.contains('chat-window-normal')).toBe(true);
      });
    });
  });

  describe('Config change session reset', () => {
    it('should clear session when chatbotId changes', async () => {
      const page = await newSpecPage({
        components: [OcsChat],
        html: `<open-chat-studio-widget chatbot-id="bot-1" visible="true"></open-chat-studio-widget>`,
      });

      const component = page.rootInstance as OcsChat;
      component.activeSessionId = 'session-123';
      component.messages = [{ created_at: new Date().toISOString(), role: 'user', content: 'Hello', attachments: [] }];

      // Change chatbotId
      page.root!.setAttribute('chatbot-id', 'bot-2');
      await page.waitForChanges();

      expect(component.activeSessionId).toBeUndefined();
      expect(component.messages).toEqual([]);
      expect(component.isTyping).toBe(false);
      expect(component.currentPollTaskId).toBe('');
    });

    it('should clear session when versionNumber changes', async () => {
      const page = await newSpecPage({
        components: [OcsChat],
        html: `<open-chat-studio-widget chatbot-id="bot-1" visible="true" version-number="1"></open-chat-studio-widget>`,
      });

      const component = page.rootInstance as OcsChat;
      component.activeSessionId = 'session-123';
      component.messages = [{ created_at: new Date().toISOString(), role: 'user', content: 'Hello', attachments: [] }];

      // Change versionNumber
      page.root!.setAttribute('version-number', '2');
      await page.waitForChanges();

      expect(component.activeSessionId).toBeUndefined();
      expect(component.messages).toEqual([]);
      expect(component.isTyping).toBe(false);
      expect(component.currentPollTaskId).toBe('');
    });

    it('should increment sessionEpoch on config change to guard against stale responses', async () => {
      const page = await newSpecPage({
        components: [OcsChat],
        html: `<open-chat-studio-widget chatbot-id="bot-1" visible="true"></open-chat-studio-widget>`,
      });

      const component = page.rootInstance as OcsChat;
      const initialEpoch = (component as any).sessionEpoch;

      page.root!.setAttribute('chatbot-id', 'bot-2');
      await page.waitForChanges();

      expect((component as any).sessionEpoch).toBe(initialEpoch + 1);
    });
  });

  describe('Combined Welcome Messages and Starter Questions', () => {
    it('should display both welcome messages and starter questions from translations', async () => {
      const page = await newSpecPage({
        components: [OcsChat],
        html: `<open-chat-studio-widget chatbot-id="test-bot" visible="true"></open-chat-studio-widget>`,
      });

      const component = page.rootInstance as OcsChat;

      component.translationManager = new TranslationManager('en', {
        'content.welcomeMessages': ['Welcome!'],
        'content.starterQuestions': ['How can I help?'],
      });

      component.messages = [];
      component.activeSessionId = 'test-session';

      await page.waitForChanges();

      const welcomeMessages = page.root?.shadowRoot?.querySelector('.welcome-messages');
      const starterQuestions = page.root?.shadowRoot?.querySelector('.starter-questions');

      expect(welcomeMessages).toBeTruthy();
      expect(starterQuestions).toBeTruthy();
    });
  });

  describe('Public event API', () => {
    function collectEvents(widget: Element, ...names: string[]): Record<string, CustomEvent[]> {
      const collected: Record<string, CustomEvent[]> = {};
      for (const name of names) {
        collected[name] = [];
        widget.addEventListener(name, (e: Event) => collected[name].push(e as CustomEvent));
      }
      return collected;
    }

    it('dispatches ocs:open when the widget becomes visible', async () => {
      const page = await newSpecPage({
        components: [OcsChat],
        html: `<open-chat-studio-widget chatbot-id="bot-1"></open-chat-studio-widget>`,
      });

      const events = collectEvents(page.root!, 'ocs:open', 'ocs:close');

      page.root!.visible = true;
      await page.waitForChanges();

      expect(events['ocs:open']).toHaveLength(1);
      expect(events['ocs:close']).toHaveLength(0);
    });

    it('dispatches ocs:close when the widget is hidden', async () => {
      const page = await newSpecPage({
        components: [OcsChat],
        html: `<open-chat-studio-widget chatbot-id="bot-1" visible></open-chat-studio-widget>`,
      });

      const events = collectEvents(page.root!, 'ocs:open', 'ocs:close');

      page.root!.visible = false;
      await page.waitForChanges();

      expect(events['ocs:close']).toHaveLength(1);
      expect(events['ocs:open']).toHaveLength(0);
    });

    it('ocs:open and ocs:close are composed and bubbling', async () => {
      const page = await newSpecPage({
        components: [OcsChat],
        html: `<open-chat-studio-widget chatbot-id="bot-1"></open-chat-studio-widget>`,
      });

      let capturedOpen: CustomEvent | null = null;
      page.root!.addEventListener('ocs:open', (e: Event) => {
        capturedOpen = e as CustomEvent;
      });

      page.root!.visible = true;
      await page.waitForChanges();

      expect(capturedOpen).not.toBeNull();
      expect((capturedOpen as unknown as CustomEvent).bubbles).toBe(true);
      expect((capturedOpen as unknown as CustomEvent).composed).toBe(true);
    });

    it('ocs:message:before-send fires before ocs:message:sent', async () => {
      const page = await newSpecPage({
        components: [OcsChat],
        html: `<open-chat-studio-widget chatbot-id="bot-1" visible></open-chat-studio-widget>`,
      });

      const component = page.rootInstance as OcsChat;
      component.activeSessionId = 'session-abc';

      const order: string[] = [];
      page.root!.addEventListener('ocs:message:before-send', () => order.push('before'));
      page.root!.addEventListener('ocs:message:sent', () => order.push('sent'));

      (component as any).chatService = {
        sendMessage: jest.fn().mockResolvedValue({ status: 'processing', task_id: 'task-1' }),
        pollTask: jest.fn().mockReturnValue({ cancel: jest.fn() }),
        stopMessagePolling: jest.fn(),
        startMessagePolling: jest.fn().mockReturnValue({ stop: jest.fn() }),
        setSessionToken: jest.fn(),
      };

      await (component as any).sendMessage('hello');

      expect(order[0]).toBe('before');
      expect(order[1]).toBe('sent');
    });

    it('ocs:message:before-send detail contains message and sessionId', async () => {
      const page = await newSpecPage({
        components: [OcsChat],
        html: `<open-chat-studio-widget chatbot-id="bot-1" visible></open-chat-studio-widget>`,
      });

      const component = page.rootInstance as OcsChat;
      component.activeSessionId = 'session-xyz';

      let detail: any = null;
      page.root!.addEventListener('ocs:message:before-send', (e: Event) => {
        detail = (e as CustomEvent).detail;
      });

      (component as any).chatService = {
        sendMessage: jest.fn().mockResolvedValue({ status: 'processing', task_id: 't1' }),
        pollTask: jest.fn().mockReturnValue({ cancel: jest.fn() }),
        stopMessagePolling: jest.fn(),
        startMessagePolling: jest.fn().mockReturnValue({ stop: jest.fn() }),
        setSessionToken: jest.fn(),
      };

      await (component as any).sendMessage('test message');

      expect(detail).toEqual({ message: 'test message', sessionId: 'session-xyz' });
    });

    it('ocs:message:before-send allows pageContext to be updated before the API call', async () => {
      const page = await newSpecPage({
        components: [OcsChat],
        html: `<open-chat-studio-widget chatbot-id="bot-1" visible></open-chat-studio-widget>`,
      });

      const component = page.rootInstance as OcsChat;
      component.activeSessionId = 'session-ctx';

      const freshCtx = { url: '/new-page', title: 'New Page' };
      page.root!.addEventListener('ocs:message:before-send', () => {
        // Simulate what an embedder would do: update pageContext at send time
        page.root!.pageContext = freshCtx;
      });

      let capturedRequestBody: any = null;
      (component as any).chatService = {
        sendMessage: jest.fn().mockImplementation((_: string, body: unknown) => {
          capturedRequestBody = body;
          return Promise.resolve({ status: 'processing', task_id: 't1' });
        }),
        pollTask: jest.fn().mockReturnValue({ cancel: jest.fn() }),
        stopMessagePolling: jest.fn(),
        startMessagePolling: jest.fn().mockReturnValue({ stop: jest.fn() }),
        setSessionToken: jest.fn(),
      };

      await (component as any).sendMessage('hello');

      expect(capturedRequestBody.context).toEqual(freshCtx);
    });

    it('dispatches ocs:message:received for each non-user message via startMessagePolling', async () => {
      const page = await newSpecPage({
        components: [OcsChat],
        html: `<open-chat-studio-widget chatbot-id="bot-1" visible></open-chat-studio-widget>`,
      });

      const component = page.rootInstance as OcsChat;
      component.activeSessionId = 'poll-session';

      const receivedEvents: CustomEvent[] = [];
      page.root!.addEventListener('ocs:message:received', (e: Event) => {
        receivedEvents.push(e as CustomEvent);
      });

      let capturedCallbacks: any = null;
      (component as any).chatService = {
        sendMessage: jest.fn(),
        pollTask: jest.fn().mockReturnValue({ cancel: jest.fn() }),
        stopMessagePolling: jest.fn(),
        startMessagePolling: jest.fn().mockImplementation((_sessionId: string, callbacks: any) => {
          capturedCallbacks = callbacks;
          return { stop: jest.fn() };
        }),
        setSessionToken: jest.fn(),
      };

      // Trigger startMessagePolling by calling the private method directly
      (component as any).startMessagePolling();

      const assistantMessage = { created_at: '2026-01-01T00:00:00Z', role: 'assistant', content: 'Hello!' };
      const userMessage = { created_at: '2026-01-01T00:00:01Z', role: 'user', content: 'Hi' };

      capturedCallbacks.onMessages([assistantMessage, userMessage]);
      await page.waitForChanges();

      // Only the assistant message should fire ocs:message:received
      expect(receivedEvents).toHaveLength(1);
      expect(receivedEvents[0].detail).toEqual({
        message: { ...assistantMessage },
        sessionId: 'poll-session',
      });
    });

    // ocs:session:started is tested in ocs-chat_session_handling.spec.tsx
    // which has the full ChatSessionService module mock wired up.

    it('dispatches ocs:session:ended with sessionId when session ends', async () => {
      const page = await newSpecPage({
        components: [OcsChat],
        html: `<open-chat-studio-widget chatbot-id="bot-1" visible></open-chat-studio-widget>`,
      });

      const component = page.rootInstance as OcsChat;
      component.activeSessionId = 'ending-session';
      (component as any).chatService = { stopMessagePolling: jest.fn() };

      let detail: any = null;
      page.root!.addEventListener('ocs:session:ended', (e: Event) => {
        detail = (e as CustomEvent).detail;
      });

      (component as any).handleSessionEnded();

      expect(detail).toEqual({ sessionId: 'ending-session' });
    });
  });
});
