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
        'content.welcomeMessages': ['Hello from translations!', 'Welcome to our chat.']
      });

      // Ensure no messages exist yet
      component.messages = [];
      component.sessionId = 'test-session';

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
      component.sessionId = 'test-session';

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
        'content.welcomeMessages': ['Translation message 1', 'Translation message 2']
      });

      component.messages = [];
      component.sessionId = 'test-session';

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
        'content.welcomeMessages': ['Hello from translations!']
      });

      // Add a message to the chat
      component.messages = [{
        created_at: new Date().toISOString(),
        role: 'user',
        content: 'Hello',
        attachments: []
      }];
      component.sessionId = 'test-session';

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
        'content.starterQuestions': ['What can you help me with?', 'How does this work?']
      });

      component.messages = [];
      component.sessionId = 'test-session';

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
      component.sessionId = 'test-session';

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
        'content.starterQuestions': ['Translation question 1?', 'Translation question 2?', 'Translation question 3?']
      });

      component.messages = [];
      component.sessionId = 'test-session';

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
        'content.starterQuestions': ['Question 1?', 'Question 2?']
      });

      // Add a message to the chat
      component.messages = [{
        created_at: new Date().toISOString(),
        role: 'user',
        content: 'Hello',
        attachments: []
      }];
      component.sessionId = 'test-session';

      await page.waitForChanges();

      // Starter questions should not be displayed
      const starterQuestions = page.root?.shadowRoot?.querySelector('.starter-questions');
      expect(starterQuestions).toBeFalsy();
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
        'content.starterQuestions': ['How can I help?']
      });

      component.messages = [];
      component.sessionId = 'test-session';

      await page.waitForChanges();

      const welcomeMessages = page.root?.shadowRoot?.querySelector('.welcome-messages');
      const starterQuestions = page.root?.shadowRoot?.querySelector('.starter-questions');

      expect(welcomeMessages).toBeTruthy();
      expect(starterQuestions).toBeTruthy();
    });
  });
});
