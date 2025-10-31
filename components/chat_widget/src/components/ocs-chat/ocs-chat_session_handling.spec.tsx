import { newSpecPage } from '@stencil/core/testing';
import { OcsChat } from './ocs-chat';

// Create mock functions at the module level
const mockStartSession = jest.fn();
const mockSendMessage = jest.fn();
const mockPollTask = jest.fn();
const mockStartMessagePolling = jest.fn();
const mockStopMessagePolling = jest.fn();

// Mock the ChatSessionService module
jest.mock('../../services/chat-session-service', () => {
  return {
    ChatSessionService: jest.fn().mockImplementation(() => ({
      startSession: mockStartSession,
      sendMessage: mockSendMessage,
      pollTask: mockPollTask,
      startMessagePolling: mockStartMessagePolling,
      stopMessagePolling: mockStopMessagePolling,
    })),
  };
});

// Helper to create fetch mock with configurable session ID
function setupFetchMock(sessionId = 'test-session-id', taskId = 'test-task-id') {
  return jest.fn().mockImplementation((url: string) => {
    if (url.includes('/api/chat/start/')) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          session_id: sessionId,
          chatbot: {},
          participant: {},
        }),
      } as Response);
    }
    if (url.includes('/api/chat/send/')) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          task_id: taskId,
          status: 'processing',
        }),
      } as Response);
    }
    return Promise.reject(new Error('Unexpected fetch call'));
  });
}

describe('ocs-chat session creation', () => {
  beforeEach(() => {
    // Clear all mocks before each test
    jest.clearAllMocks();

    // Setup default mock implementations
    mockStartSession.mockResolvedValue({
      session_id: 'test-session-id',
    });

    mockSendMessage.mockResolvedValue({
      status: 'success',
      task_id: 'test-task-id',
    });

    mockPollTask.mockReturnValue({
      cancel: jest.fn(),
    });

    mockStartMessagePolling.mockReturnValue({
      stop: jest.fn(),
    });

    // Mock fetch API using helper
    global.fetch = setupFetchMock();

    // Mock localStorage
    const localStorageMock = {
      getItem: jest.fn(),
      setItem: jest.fn(),
      removeItem: jest.fn(),
      clear: jest.fn(),
    };
    Object.defineProperty(window, 'localStorage', {
      value: localStorageMock,
      writable: true,
    });

    // Mock crypto.getRandomValues for user ID generation
    Object.defineProperty(window, 'crypto', {
      value: {
        getRandomValues: jest.fn((arr: Uint8Array) => {
          for (let i = 0; i < arr.length; i++) {
            arr[i] = Math.floor(Math.random() * 256);
          }
          return arr;
        }),
      },
      writable: true,
    });
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('should not automatically start a session when component loads', async () => {
    const page = await newSpecPage({
      components: [OcsChat],
      html: '<open-chat-studio-widget chatbot-id="test-bot" visible="false"></open-chat-studio-widget>',
    });

    // Wait for any async operations
    await page.waitForChanges();

    expect(mockStartSession).not.toHaveBeenCalled();
    expect(page.rootInstance.sessionId).toBeUndefined();
  });

  it('should not automatically start a session when widget becomes visible', async () => {
    const page = await newSpecPage({
      components: [OcsChat],
      html: '<open-chat-studio-widget chatbot-id="test-bot" visible="false"></open-chat-studio-widget>',
    });

    // Wait for initial load
    await page.waitForChanges();

    // Make widget visible
    page.rootInstance.visible = true;
    await page.waitForChanges();

    expect(mockStartSession).not.toHaveBeenCalled();
    expect(page.rootInstance.sessionId).toBeUndefined();
  });

  it('should start a session when user sends first message', async () => {
    const page = await newSpecPage({
      components: [OcsChat],
      html: '<open-chat-studio-widget chatbot-id="test-bot" visible="true"></open-chat-studio-widget>',
    });

    await page.waitForChanges();

    // Verify no session exists initially
    expect(page.rootInstance.sessionId).toBeUndefined();
    expect(global.fetch).not.toHaveBeenCalled();

    // Simulate user sending a message
    page.rootInstance.messageInput = 'Hello, world!';
    const sendPromise = page.rootInstance.sendMessage('Hello, world!');

    // Wait for the async operation to complete
    await sendPromise;
    await page.waitForChanges();

    // Verify fetch was called to start a session
    expect(global.fetch).toHaveBeenCalled();
    const fetchCalls = (global.fetch as jest.Mock).mock.calls;
    const startSessionCall = fetchCalls.find(call => call[0].includes('/api/chat/start/'));
    expect(startSessionCall).toBeDefined();

    // Verify session ID was set
    expect(page.rootInstance.sessionId).toBe('test-session-id');

    // Verify the user message was added to messages
    expect(page.rootInstance.messages.length).toBeGreaterThan(0);
    const userMessage = page.rootInstance.messages.find((m: any) => m.role === 'user');
    expect(userMessage).toBeDefined();
    expect(userMessage.content).toBe('Hello, world!');
  });

  it('should load existing session from localStorage without creating new one', async () => {
    const existingSessionId = 'existing-session-id';
    const existingMessages = [
      {
        created_at: new Date().toISOString(),
        role: 'user',
        content: 'Previous message',
        attachments: [],
      },
    ];

    // Mock localStorage to return existing session data
    const mockGetItem = jest.fn((key: string) => {
      if (key === 'ocs-chat-session-test-bot') {
        return existingSessionId;
      }
      if (key === 'ocs-chat-messages-test-bot') {
        return JSON.stringify(existingMessages);
      }
      if (key === 'ocs-chat-activity-test-bot') {
        return new Date().toISOString();
      }
      return null;
    });

    (window.localStorage.getItem as jest.Mock) = mockGetItem;

    const page = await newSpecPage({
      components: [OcsChat],
      html: '<open-chat-studio-widget chatbot-id="test-bot" visible="true" persistent-session="true"></open-chat-studio-widget>',
    });

    await page.waitForChanges();

    // Verify existing session was loaded
    expect(page.rootInstance.sessionId).toBe(existingSessionId);
    expect(page.rootInstance.messages).toEqual(existingMessages);

    // Verify no fetch call was made to start a new session
    const fetchCalls = (global.fetch as jest.Mock).mock.calls;
    const startSessionCall = fetchCalls.find(call => call[0] && call[0].includes('/api/chat/start/'));
    expect(startSessionCall).toBeUndefined();
  });

  it('should handle messages sent before session is created', async () => {
    const page = await newSpecPage({
      components: [OcsChat],
      html: '<open-chat-studio-widget chatbot-id="test-bot" visible="true"></open-chat-studio-widget>',
    });

    await page.waitForChanges();

    // Send first message, which will start creating a session
    await page.rootInstance.sendMessage('Message 1');
    await page.waitForChanges();

    expect(page.rootInstance.sessionId).toBe('test-session-id');

    // Now send a second message - should use existing session
    jest.clearAllMocks(); // Clear previous fetch calls
    await page.rootInstance.sendMessage('Message 2');
    await page.waitForChanges();

    // Verify no new session start was made for the second message
    const fetchCalls = (global.fetch as jest.Mock).mock.calls;
    const startSessionCalls = fetchCalls.filter(call => call[0] && call[0].includes('/api/chat/start/'));
    expect(startSessionCalls.length).toBe(0);

    // Verify both messages were added
    expect(page.rootInstance.messages.length).toBeGreaterThanOrEqual(2);
  });

  it('should clear session and allow new session on next message when clearSession is called', async () => {
    // First, create a session by sending a message
    const page = await newSpecPage({
      components: [OcsChat],
      html: '<open-chat-studio-widget chatbot-id="test-bot" visible="true"></open-chat-studio-widget>',
    });

    await page.waitForChanges();

    // Send first message to create session
    await page.rootInstance.sendMessage('First message');
    await page.waitForChanges();

    expect(page.rootInstance.sessionId).toBe('test-session-id');
    const initialMessages = page.rootInstance.messages.length;
    expect(initialMessages).toBeGreaterThan(0);

    // Clear the session
    await page.rootInstance.clearSession();
    await page.waitForChanges();

    expect(page.rootInstance.sessionId).toBeUndefined();
    expect(page.rootInstance.messages).toEqual([]);

    // Update fetch mock to return a new session ID using helper
    global.fetch = setupFetchMock('new-session-id', 'test-task-id-2');

    // Send another message, which should create a new session
    await page.rootInstance.sendMessage('New session message');
    await page.waitForChanges();

    expect(page.rootInstance.sessionId).toBe('new-session-id');
    expect(page.rootInstance.messages.length).toBeGreaterThan(0);
  });

  it('should handle starter questions by creating session on first click', async () => {
    const page = await newSpecPage({
      components: [OcsChat],
      html: `
        <open-chat-studio-widget
          chatbot-id="test-bot"
          visible="true"
          starter-questions='["Question 1", "Question 2"]'
        ></open-chat-studio-widget>
      `,
    });

    await page.waitForChanges();

    // Verify no session initially
    expect(page.rootInstance.sessionId).toBeUndefined();

    // Click a starter question (which internally calls sendMessage)
    await page.rootInstance.handleStarterQuestionClick('Question 1');
    await page.waitForChanges();

    // Verify session was created
    expect(page.rootInstance.sessionId).toBe('test-session-id');

    // Verify the question was added as a user message
    const userMessage = page.rootInstance.messages.find((m: any) => m.role === 'user');
    expect(userMessage).toBeDefined();
    expect(userMessage.content).toBe('Question 1');

    // Verify fetch was called
    const fetchCalls = (global.fetch as jest.Mock).mock.calls;
    const startSessionCall = fetchCalls.find(call => call[0] && call[0].includes('/api/chat/start/'));
    expect(startSessionCall).toBeDefined();
  });

  it('should show input area even without a session', async () => {
    const page = await newSpecPage({
      components: [OcsChat],
      html: '<open-chat-studio-widget chatbot-id="test-bot" visible="true"></open-chat-studio-widget>',
    });

    await page.waitForChanges();

    // Verify no session exists
    expect(page.rootInstance.sessionId).toBeUndefined();

    // Check that input area is rendered
    const inputArea = page.root?.shadowRoot?.querySelector('.input-area');
    expect(inputArea).toBeTruthy();

    // Verify textarea is present
    const textarea = page.root?.shadowRoot?.querySelector('.message-textarea');
    expect(textarea).toBeTruthy();
  });

  it('should disable input during session creation', async () => {
    // Make the fetch mock delay to simulate loading
    let resolveStartSession: (value: any) => void;
    const startSessionPromise = new Promise(resolve => {
      resolveStartSession = resolve;
    });

    global.fetch = jest.fn().mockImplementation((url: string) => {
      if (url.includes('/api/chat/start/')) {
        return startSessionPromise.then(() =>
          Promise.resolve({
            ok: true,
            json: () =>
              Promise.resolve({
                session_id: 'test-session-id',
                chatbot: {},
                participant: {},
              }),
          } as Response)
        );
      }
      if (url.includes('/api/chat/send/')) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              task_id: 'test-task-id',
              status: 'processing',
            }),
        } as Response);
      }
      return Promise.reject(new Error('Unexpected fetch call'));
    });

    const page = await newSpecPage({
      components: [OcsChat],
      html: '<open-chat-studio-widget chatbot-id="test-bot" visible="true"></open-chat-studio-widget>',
    });

    await page.waitForChanges();

    // Start sending a message (triggers session creation)
    const sendPromise = page.rootInstance.sendMessage('Test message');
    await page.waitForChanges();

    // Check that loading state is active
    expect(page.rootInstance.isLoading).toBe(true);

    // Resolve the delayed promise
    resolveStartSession!(null);
    await sendPromise;
    await page.waitForChanges();

    // Verify loading state is cleared
    expect(page.rootInstance.isLoading).toBe(false);
  });
});
