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

  describe('Navigation-triggered page context updates', () => {
    let originalHref: PropertyDescriptor | undefined;

    beforeEach(() => {
      // Allow tests to control window.location.href
      originalHref = Object.getOwnPropertyDescriptor(window, 'location');
    });

    afterEach(() => {
      jest.restoreAllMocks();
    });

    function setUrl(url: string) {
      Object.defineProperty(window, 'location', {
        value: { href: url, pathname: new URL(url).pathname, hash: new URL(url).hash },
        writable: true,
        configurable: true,
      });
    }

    it('sets internalPageContext to minimal URL context on popstate when no pageContext prop is set', async () => {
      setUrl('https://example.com/page-a');

      const page = await newSpecPage({
        components: [OcsChat],
        html: `<open-chat-studio-widget chatbot-id="bot-1"></open-chat-studio-widget>`,
      });

      const component = page.rootInstance as OcsChat;
      // No pageContext prop set
      expect(component.pageContext).toBeUndefined();

      // Simulate SPA navigation
      setUrl('https://example.com/page-b');
      window.dispatchEvent(new Event('popstate'));
      await page.waitForChanges();

      expect((component as any).internalPageContext).toEqual({ url: 'https://example.com/page-b' });
    });

    it('re-loads pageContext prop on popstate when pageContext is set', async () => {
      setUrl('https://example.com/page-a');

      const page = await newSpecPage({
        components: [OcsChat],
        html: `<open-chat-studio-widget chatbot-id="bot-1"></open-chat-studio-widget>`,
      });

      const component = page.rootInstance as OcsChat;
      const ctx = { url: '/page-a', title: 'Page A' };
      component.pageContext = ctx;
      await page.waitForChanges();

      // Clear internalPageContext as if a message was already sent
      (component as any).internalPageContext = undefined;

      // Simulate SPA navigation
      setUrl('https://example.com/page-b');
      const newCtx = { url: '/page-b', title: 'Page B' };
      component.pageContext = newCtx;
      window.dispatchEvent(new Event('popstate'));
      await page.waitForChanges();

      expect((component as any).internalPageContext).toEqual(newCtx);
    });

    it('does not update internalPageContext when URL has not changed', async () => {
      const page = await newSpecPage({
        components: [OcsChat],
        html: `<open-chat-studio-widget chatbot-id="bot-1"></open-chat-studio-widget>`,
      });

      const component = page.rootInstance as OcsChat;
      // componentDidLoad already set currentPageUrl = window.location.href.
      // Clearing internalPageContext simulates a state after a message was sent.
      (component as any).internalPageContext = undefined;

      // Dispatch popstate without changing the URL — should be a no-op.
      window.dispatchEvent(new Event('popstate'));
      await page.waitForChanges();

      expect((component as any).internalPageContext).toBeUndefined();
    });

    it('responds to ocs-navigation custom event', async () => {
      setUrl('https://example.com/page-a');

      const page = await newSpecPage({
        components: [OcsChat],
        html: `<open-chat-studio-widget chatbot-id="bot-1"></open-chat-studio-widget>`,
      });

      const component = page.rootInstance as OcsChat;
      setUrl('https://example.com/page-b');
      window.dispatchEvent(new CustomEvent('ocs-navigation'));
      await page.waitForChanges();

      expect((component as any).internalPageContext).toEqual({ url: 'https://example.com/page-b' });
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
});
