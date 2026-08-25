import { newSpecPage } from '@stencil/core/testing';
import { OcsChat } from './ocs-chat';
import { SessionAccessError } from '../../services/chat-session-service';

// Create mock functions at the module level
const mockStartSession = jest.fn();
const mockSendMessage = jest.fn();
const mockPollTask = jest.fn();
const mockStartMessagePolling = jest.fn();
const mockStopMessagePolling = jest.fn();
const mockFetchAllMessages = jest.fn();
// Mock the ChatSessionService module
jest.mock('../../services/chat-session-service', () => {
  const actual = jest.requireActual('../../services/chat-session-service');
  return {
    ...actual,
    ChatSessionService: jest.fn().mockImplementation(() => ({
      startSession: mockStartSession,
      sendMessage: mockSendMessage,
      pollTask: mockPollTask,
      startMessagePolling: mockStartMessagePolling,
      stopMessagePolling: mockStopMessagePolling,
      fetchAllMessages: mockFetchAllMessages,
    })),
  };
});

// Helper to create fetch mock with configurable session ID
function setupFetchMock(sessionId = 'test-session-id', taskId = 'test-task-id') {
  return jest.fn().mockImplementation((url: string) => {
    if (url.includes('/api/chat/start/')) {
      return Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({
            session_id: sessionId,
            chatbot: {},
            participant: {},
          }),
      } as Response);
    }
    if (url.includes('/api/chat/send/')) {
      return Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({
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
      configurable: true,
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

  afterEach(async () => {
    // Small delay to allow any pending promises to resolve
    await new Promise(resolve => setTimeout(resolve, 0));
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
    expect(page.rootInstance.activeSessionId).toBeUndefined();
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
    expect(page.rootInstance.activeSessionId).toBeUndefined();
  });

  it('should start a session when user sends first message', async () => {
    const page = await newSpecPage({
      components: [OcsChat],
      html: '<open-chat-studio-widget chatbot-id="test-bot" visible="true"></open-chat-studio-widget>',
    });

    await page.waitForChanges();

    // Verify no session exists initially
    expect(page.rootInstance.activeSessionId).toBeUndefined();
    expect(global.fetch).not.toHaveBeenCalled();

    // Simulate user sending a message
    page.rootInstance.messageInput = 'Hello, world!';
    await page.rootInstance.sendMessage('Hello, world!');

    // Wait for the async operation to complete
    await page.waitForChanges();

    // Verify fetch was called to start a session
    expect(global.fetch).toHaveBeenCalled();
    const fetchCalls = (global.fetch as jest.Mock).mock.calls;
    const startSessionCall = fetchCalls.find(call => call[0].includes('/api/chat/start/'));
    expect(startSessionCall).toBeDefined();

    // Verify session ID was set
    expect(page.rootInstance.activeSessionId).toBe('test-session-id');

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
    (window.localStorage.getItem as jest.Mock) = jest.fn((key: string) => {
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

    const page = await newSpecPage({
      components: [OcsChat],
      html: '<open-chat-studio-widget chatbot-id="test-bot" visible="true" persistent-session="true"></open-chat-studio-widget>',
    });

    await page.waitForChanges();

    // Verify existing session was loaded
    expect(page.rootInstance.activeSessionId).toBe(existingSessionId);
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

    expect(page.rootInstance.activeSessionId).toBe('test-session-id');

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

    expect(page.rootInstance.activeSessionId).toBe('test-session-id');
    expect(page.rootInstance.messages.length).toBeGreaterThan(0);

    // Clear the session
    await page.rootInstance.clearSession();
    await page.waitForChanges();

    expect(page.rootInstance.activeSessionId).toBeUndefined();
    expect(page.rootInstance.messages).toEqual([]);

    // Update fetch mock to return a new session ID using helper
    global.fetch = setupFetchMock('new-session-id', 'test-task-id-2');

    // Send another message, which should create a new session
    await page.rootInstance.sendMessage('New session message');
    await page.waitForChanges();

    expect(page.rootInstance.activeSessionId).toBe('new-session-id');
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
    expect(page.rootInstance.activeSessionId).toBeUndefined();

    // Click a starter question (which internally calls sendMessage)
    await page.rootInstance.handleStarterQuestionClick('Question 1');
    await page.waitForChanges();

    // Verify session was created
    expect(page.rootInstance.activeSessionId).toBe('test-session-id');

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
    expect(page.rootInstance.activeSessionId).toBeUndefined();

    // Check that input area is rendered
    const inputArea = page.root?.shadowRoot?.querySelector('.input-area');
    expect(inputArea).toBeTruthy();

    // Verify textarea is present
    const textarea = page.root?.shadowRoot?.querySelector('.message-textarea');
    expect(textarea).toBeTruthy();
  });
});

describe('ocs-chat progress message during polling', () => {
  beforeEach(() => {
    jest.clearAllMocks();

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
  });

  afterEach(async () => {
    await new Promise(resolve => setTimeout(resolve, 0));
    jest.restoreAllMocks();
  });

  it('should display progress message in typing indicator when set', async () => {
    const page = await newSpecPage({
      components: [OcsChat],
      html: '<open-chat-studio-widget chatbot-id="test-bot" visible="true"></open-chat-studio-widget>',
    });

    const component = page.rootInstance;
    component.activeSessionId = 'test-session';
    component.isTyping = true;
    component.typingProgressMessage = 'Searching documents...';

    await page.waitForChanges();

    const typingText = page.root?.shadowRoot?.querySelector('.typing-text span');
    expect(typingText?.textContent).toBe('Searching documents...');
  });

  it('should display default typing text when no progress message is set', async () => {
    const page = await newSpecPage({
      components: [OcsChat],
      html: '<open-chat-studio-widget chatbot-id="test-bot" visible="true"></open-chat-studio-widget>',
    });

    const component = page.rootInstance;
    component.activeSessionId = 'test-session';
    component.isTyping = true;
    component.typingProgressMessage = '';

    await page.waitForChanges();

    const typingText = page.root?.shadowRoot?.querySelector('.typing-text span');
    expect(typingText?.textContent).toBeTruthy();
    expect(typingText?.textContent).not.toBe('');
  });

  it('should update displayed text when progress message changes', async () => {
    const page = await newSpecPage({
      components: [OcsChat],
      html: '<open-chat-studio-widget chatbot-id="test-bot" visible="true"></open-chat-studio-widget>',
    });

    const component = page.rootInstance;
    component.activeSessionId = 'test-session';
    component.isTyping = true;
    component.typingProgressMessage = 'Step 1...';

    await page.waitForChanges();

    let typingText = page.root?.shadowRoot?.querySelector('.typing-text span');
    expect(typingText?.textContent).toBe('Step 1...');

    component.typingProgressMessage = 'Step 2...';
    await page.waitForChanges();

    typingText = page.root?.shadowRoot?.querySelector('.typing-text span');
    expect(typingText?.textContent).toBe('Step 2...');
  });

  it('should fall back to default text when progress message is cleared', async () => {
    const page = await newSpecPage({
      components: [OcsChat],
      html: '<open-chat-studio-widget chatbot-id="test-bot" visible="true"></open-chat-studio-widget>',
    });

    const component = page.rootInstance;
    component.activeSessionId = 'test-session';
    component.isTyping = true;
    component.typingProgressMessage = 'Working...';

    await page.waitForChanges();

    let typingText = page.root?.shadowRoot?.querySelector('.typing-text span');
    expect(typingText?.textContent).toBe('Working...');

    // Clear the progress message
    component.typingProgressMessage = '';
    await page.waitForChanges();

    typingText = page.root?.shadowRoot?.querySelector('.typing-text span');
    expect(typingText?.textContent).toBeTruthy();
    expect(typingText?.textContent).not.toBe('Working...');
  });

  it('should not show typing indicator or progress message when not typing', async () => {
    const page = await newSpecPage({
      components: [OcsChat],
      html: '<open-chat-studio-widget chatbot-id="test-bot" visible="true"></open-chat-studio-widget>',
    });

    const component = page.rootInstance;
    component.activeSessionId = 'test-session';
    component.isTyping = false;
    component.typingProgressMessage = 'Should not appear';

    await page.waitForChanges();

    const typingText = page.root?.shadowRoot?.querySelector('.typing-text');
    expect(typingText).toBeFalsy();
  });
});

describe('ocs-chat localStorage blocked (SecurityError)', () => {
  beforeEach(() => {
    jest.clearAllMocks();

    mockStartSession.mockResolvedValue({ session_id: 'test-session-id' });
    mockSendMessage.mockResolvedValue({ status: 'success', task_id: 'test-task-id' });
    mockPollTask.mockReturnValue({ cancel: jest.fn() });
    mockStartMessagePolling.mockReturnValue({ stop: jest.fn() });

    global.fetch = setupFetchMock();

    const throwSecurityError = () => {
      throw new DOMException('The operation is insecure.', 'SecurityError');
    };
    const localStorageMock = {
      getItem: jest.fn(throwSecurityError),
      setItem: jest.fn(throwSecurityError),
      removeItem: jest.fn(throwSecurityError),
      clear: jest.fn(throwSecurityError),
    };
    Object.defineProperty(window, 'localStorage', {
      value: localStorageMock,
      writable: true,
    });

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

  afterEach(async () => {
    await new Promise(resolve => setTimeout(resolve, 0));
    jest.restoreAllMocks();
  });

  it('starts a session successfully when localStorage throws on read and write', async () => {
    const page = await newSpecPage({
      components: [OcsChat],
      html: '<open-chat-studio-widget chatbot-id="test-bot" visible="true"></open-chat-studio-widget>',
    });
    await page.waitForChanges();

    await page.rootInstance.sendMessage('Hello');
    await page.waitForChanges();

    expect(page.rootInstance.activeSessionId).toBe('test-session-id');
    expect(page.rootInstance.error).toBeFalsy();
    expect(page.rootInstance.generatedUserId).toMatch(/^ocs:[0-9a-f-]{36}$/);
  });

  it('reuses the same in-memory user id across calls when localStorage is blocked', async () => {
    const page = await newSpecPage({
      components: [OcsChat],
      html: '<open-chat-studio-widget chatbot-id="test-bot" visible="true"></open-chat-studio-widget>',
    });
    await page.waitForChanges();

    const firstId = page.rootInstance.getOrGenerateUserId();
    const secondId = page.rootInstance.getOrGenerateUserId();

    expect(firstId).toMatch(/^ocs:[0-9a-f-]{36}$/);
    expect(secondId).toBe(firstId);
  });

  it('does not throw during componentWillLoad when localStorage is blocked', async () => {
    await expect(
      newSpecPage({
        components: [OcsChat],
        html: '<open-chat-studio-widget chatbot-id="test-bot" visible="true" persistent-session="true"></open-chat-studio-widget>',
      }),
    ).resolves.toBeDefined();
  });
});

describe('ocs-chat storage getter blocked', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockStartSession.mockResolvedValue({ session_id: 'test-session-id' });
    mockSendMessage.mockResolvedValue({ status: 'success', task_id: 'test-task-id' });
    mockPollTask.mockReturnValue({ cancel: jest.fn() });
    mockStartMessagePolling.mockReturnValue({ stop: jest.fn() });
    global.fetch = setupFetchMock();

    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      get() {
        throw new DOMException('blocked', 'SecurityError');
      },
    });

    Object.defineProperty(window, 'crypto', {
      value: {
        getRandomValues: jest.fn((arr: Uint8Array) => {
          for (let i = 0; i < arr.length; i++) {
            arr[i] = Math.floor(Math.random() * 256);
          }
          return arr;
        }),
        randomUUID: jest.fn(() => '00000000-0000-4000-8000-000000000000'),
      },
      writable: true,
    });
  });

  afterEach(async () => {
    await new Promise(resolve => setTimeout(resolve, 0));
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      writable: true,
      value: {
        getItem: jest.fn(),
        setItem: jest.fn(),
        removeItem: jest.fn(),
        clear: jest.fn(),
      },
    });
    jest.restoreAllMocks();
  });

  it('renders when the localStorage getter itself throws', async () => {
    await expect(
      newSpecPage({
        components: [OcsChat],
        html: '<open-chat-studio-widget chatbot-id="test-bot" visible="true"></open-chat-studio-widget>',
      }),
    ).resolves.toBeDefined();
  });

  it('treats storage as unavailable when the getter throws', async () => {
    const page = await newSpecPage({
      components: [OcsChat],
      html: '<open-chat-studio-widget chatbot-id="test-bot" visible="true"></open-chat-studio-widget>',
    });
    await page.waitForChanges();

    expect(page.rootInstance['getStorage']()).toBeUndefined();
  });

  it('still starts a session when the storage getter throws', async () => {
    const page = await newSpecPage({
      components: [OcsChat],
      html: '<open-chat-studio-widget chatbot-id="test-bot" visible="true"></open-chat-studio-widget>',
    });
    await page.waitForChanges();
    const svc = page.rootInstance['getChatService']();
    jest.spyOn(svc, 'startSession').mockImplementation(mockStartSession);
    jest.spyOn(svc, 'sendMessage').mockImplementation(mockSendMessage);
    jest.spyOn(svc, 'startMessagePolling').mockImplementation(mockStartMessagePolling);
    jest.spyOn(svc, 'pollTask').mockImplementation(mockPollTask);

    await page.rootInstance.sendMessage('hello');
    await page.waitForChanges();

    expect(mockStartSession).toHaveBeenCalled();
    expect(page.rootInstance.activeSessionId).toBe('test-session-id');
  });
});

describe('ocs-chat persistent-session modes', () => {
  let localStorageMock: Record<string, jest.Mock>;
  let sessionStorageMock: Record<string, jest.Mock>;

  function storageMock(): Record<string, jest.Mock> {
    const store: Record<string, string> = {};
    return {
      getItem: jest.fn((key: string) => (key in store ? store[key] : null)),
      setItem: jest.fn((key: string, value: string) => {
        store[key] = value;
      }),
      removeItem: jest.fn((key: string) => {
        delete store[key];
      }),
      clear: jest.fn(),
    };
  }

  beforeEach(() => {
    jest.clearAllMocks();
    mockStartSession.mockResolvedValue({ session_id: 'tab-session' });
    mockSendMessage.mockResolvedValue({ status: 'success', task_id: 'task' });
    localStorageMock = storageMock();
    sessionStorageMock = storageMock();
    Object.defineProperty(window, 'localStorage', { value: localStorageMock, writable: true });
    Object.defineProperty(window, 'sessionStorage', { value: sessionStorageMock, writable: true });
    global.fetch = setupFetchMock('tab-session');
  });

  async function newPage(attr: string) {
    const page = await newSpecPage({
      components: [OcsChat],
      html: `<open-chat-studio-widget chatbot-id="test-bot" visible="true" ${attr}></open-chat-studio-widget>`,
    });
    await page.waitForChanges();
    return page;
  }

  it.each([
    ['persistent-session="tab"', 'tab'],
    ['persistent-session="true"', 'local'],
    ['', 'local'],
    ['persistent-session="false"', 'off'],
  ])('%s resolves to mode %s', async (attr, mode) => {
    const page = await newPage(attr);
    expect(page.rootInstance['getPersistenceMode']()).toBe(mode);
  });

  it('resolves the boolean false prop to off', async () => {
    const page = await newPage('');
    page.rootInstance.persistentSession = false;
    expect(page.rootInstance['getPersistenceMode']()).toBe('off');
  });

  it('writes the session to sessionStorage and not localStorage in tab mode', async () => {
    const page = await newPage('persistent-session="tab"');
    await page.rootInstance.sendMessage('hello');
    await page.waitForChanges();

    expect(sessionStorageMock.setItem).toHaveBeenCalledWith('ocs-chat-session-test-bot', 'tab-session');
    expect(localStorageMock.setItem).not.toHaveBeenCalledWith('ocs-chat-session-test-bot', expect.anything());
  });

  it('restores a session from sessionStorage in tab mode without starting a new one', async () => {
    sessionStorageMock.setItem('ocs-chat-session-test-bot', 'stored-tab-session');
    sessionStorageMock.setItem('ocs-chat-messages-test-bot', JSON.stringify([{ created_at: '2026-01-01T00:00:00Z', role: 'user', content: 'hi', attachments: [] }]));
    sessionStorageMock.setItem.mockClear();

    const page = await newPage('persistent-session="tab"');
    await new Promise(resolve => setTimeout(resolve, 0));
    await page.waitForChanges();

    expect(page.rootInstance.activeSessionId).toBe('stored-tab-session');
    expect(page.rootInstance.messages).toHaveLength(1);
    expect(mockStartSession).not.toHaveBeenCalled();
  });

  it('clears sessionStorage when the session is cleared in tab mode', async () => {
    const page = await newPage('persistent-session="tab"');
    await page.rootInstance.sendMessage('hello');
    await page.waitForChanges();

    await page.rootInstance.clearSession();

    expect(sessionStorageMock.removeItem).toHaveBeenCalledWith('ocs-chat-session-test-bot');
    expect(sessionStorageMock.removeItem).toHaveBeenCalledWith('ocs-chat-messages-test-bot');
    expect(sessionStorageMock.removeItem).toHaveBeenCalledWith('ocs-chat-token-test-bot');
  });

  it('writes nothing when persistence is off', async () => {
    const page = await newPage('persistent-session="false"');
    await page.rootInstance.sendMessage('hello');
    await page.waitForChanges();

    expect(sessionStorageMock.setItem).not.toHaveBeenCalled();
    expect(localStorageMock.setItem).not.toHaveBeenCalledWith('ocs-chat-session-test-bot', expect.anything());
  });
});

describe('ocs-chat bound session (session-id prop)', () => {
  const history = [
    { created_at: '2026-01-01T00:00:01Z', role: 'user', content: 'Hi', attachments: [] },
    { created_at: '2026-01-01T00:00:02Z', role: 'assistant', content: 'Hello!', attachments: [] },
  ];

  beforeEach(() => {
    jest.clearAllMocks();

    mockStartSession.mockResolvedValue({ session_id: 'unexpected-new-session' });
    mockSendMessage.mockResolvedValue({ status: 'processing', task_id: 'test-task-id' });
    mockPollTask.mockReturnValue({ cancel: jest.fn() });
    mockStartMessagePolling.mockReturnValue({ stop: jest.fn() });
    mockFetchAllMessages.mockResolvedValue(history);
    global.fetch = setupFetchMock();

    // localStorage holds a DIFFERENT persisted session to prove the prop wins
    const localStorageMock = {
      getItem: jest.fn((key: string) => {
        if (key === 'ocs-chat-session-test-bot') return 'stale-local-session';
        if (key === 'ocs-chat-messages-test-bot') return JSON.stringify([]);
        if (key === 'ocs-chat-activity-test-bot') return new Date().toISOString();
        return null;
      }),
      setItem: jest.fn(),
      removeItem: jest.fn(),
      clear: jest.fn(),
    };
    Object.defineProperty(window, 'localStorage', {
      value: localStorageMock,
      writable: true,
    });

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

  afterEach(async () => {
    await new Promise(resolve => setTimeout(resolve, 0));
    jest.restoreAllMocks();
  });

  async function newBoundPage() {
    const page = await newSpecPage({
      components: [OcsChat],
      html: '<open-chat-studio-widget chatbot-id="test-bot" mode="kiosk" session-id="server-session" persistent-session="true"></open-chat-studio-widget>',
    });
    // Force chatService creation now (before setTimeout fires) so we can spy on it.
    // componentDidLoad has already registered setTimeout(0) but it has not fired yet.
    const svc = page.rootInstance['getChatService']();
    jest.spyOn(svc, 'fetchAllMessages').mockImplementation(mockFetchAllMessages);
    jest.spyOn(svc, 'startMessagePolling').mockImplementation(mockStartMessagePolling);
    jest.spyOn(svc, 'sendMessage').mockImplementation(mockSendMessage);
    jest.spyOn(svc, 'startSession').mockImplementation(mockStartSession);
    jest.spyOn(svc, 'pollTask').mockImplementation(mockPollTask);
    await page.waitForChanges();
    // componentDidLoad defers history loading via setTimeout(0)
    await new Promise(resolve => setTimeout(resolve, 0));
    await page.waitForChanges();
    return page;
  }

  it('uses the session-id prop over a persisted localStorage session', async () => {
    const page = await newBoundPage();

    expect(page.rootInstance.activeSessionId).toBe('server-session');
    expect(mockStartSession).not.toHaveBeenCalled();
  });

  it('loads full message history and starts polling for the bound session', async () => {
    const page = await newBoundPage();

    expect(mockFetchAllMessages).toHaveBeenCalledWith('server-session');
    expect(page.rootInstance.messages).toEqual(history);
    expect(mockStartMessagePolling).toHaveBeenCalledWith('server-session', expect.anything());
  });

  it('does not persist session data to localStorage while bound', async () => {
    const page = await newBoundPage();

    await page.rootInstance.sendMessage('New message');
    await page.waitForChanges();

    const setItemKeys = (window.localStorage.setItem as jest.Mock).mock.calls.map(call => call[0]);
    expect(setItemKeys).not.toContain('ocs-chat-session-test-bot');
    expect(setItemKeys).not.toContain('ocs-chat-messages-test-bot');
    expect(setItemKeys).not.toContain('ocs-chat-activity-test-bot');
    expect(setItemKeys).not.toContain('ocs-chat-token-test-bot');
  });

  it('does not persist visible state in kiosk mode', async () => {
    const page = await newBoundPage();

    // Attempting to hide a kiosk widget forces it back to visible; neither
    // transition may write the visible key, which is shared with any
    // standard-mode widget for the same chatbot on other pages.
    page.rootInstance.visible = false;
    await page.waitForChanges();

    const setItemKeys = (window.localStorage.setItem as jest.Mock).mock.calls.map(call => call[0]);
    expect(setItemKeys).not.toContain('ocs-chat-visible-test-bot');
  });

  it('sends messages to the bound session without starting a new one', async () => {
    const page = await newBoundPage();

    await page.rootInstance.sendMessage('New message');
    await page.waitForChanges();

    expect(mockStartSession).not.toHaveBeenCalled();
    expect(mockSendMessage).toHaveBeenCalledWith('server-session', expect.anything());
  });

  it('stays bound to the host session and reloads history when the session is cleared', async () => {
    const page = await newBoundPage();
    expect(mockFetchAllMessages).toHaveBeenCalledTimes(1);

    await page.rootInstance.clearSession();
    await new Promise(resolve => setTimeout(resolve, 0));
    await page.waitForChanges();

    expect(page.rootInstance.activeSessionId).toBe('server-session');
    // The host-owned session cannot be cleared: its history is reloaded.
    expect(mockFetchAllMessages).toHaveBeenCalledTimes(2);
    expect(page.rootInstance.messages).toEqual(history);
  });

  it('loads history when a hidden bound widget becomes visible', async () => {
    const page = await newSpecPage({
      components: [OcsChat],
      html: '<open-chat-studio-widget chatbot-id="test-bot" session-id="server-session" visible="false"></open-chat-studio-widget>',
    });
    const svc = page.rootInstance['getChatService']();
    jest.spyOn(svc, 'fetchAllMessages').mockImplementation(mockFetchAllMessages);
    jest.spyOn(svc, 'startMessagePolling').mockImplementation(mockStartMessagePolling);
    await page.waitForChanges();
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(mockFetchAllMessages).not.toHaveBeenCalled();

    page.rootInstance.visible = true;
    await page.waitForChanges();
    await new Promise(resolve => setTimeout(resolve, 0));
    await page.waitForChanges();

    expect(mockFetchAllMessages).toHaveBeenCalledWith('server-session');
    expect(page.rootInstance.messages).toEqual(history);
  });

  it('disables the composer and stops polling when the server reports the session ended', async () => {
    const page = await newBoundPage();
    const callbacks = mockStartMessagePolling.mock.calls[0][1];

    callbacks.onSessionEnded();
    await page.waitForChanges();

    expect(page.rootInstance.sessionEnded).toBe(true);
    const textarea = page.root?.shadowRoot?.querySelector('.message-textarea') as HTMLTextAreaElement;
    expect(textarea.hasAttribute('disabled')).toBe(true);
    const sendButton = page.root?.shadowRoot?.querySelector('.send-button') as HTMLButtonElement;
    expect(sendButton.hasAttribute('disabled')).toBe(true);
    const systemMessage = page.rootInstance.messages.find((m: any) => m.role === 'system');
    expect(systemMessage).toBeDefined();

    // Sends are ignored and polling does not restart.
    await page.rootInstance.sendMessage('hello');
    expect(mockSendMessage).not.toHaveBeenCalled();
    mockStartMessagePolling.mockClear();
    page.rootInstance['startMessagePolling']();
    expect(mockStartMessagePolling).not.toHaveBeenCalled();
  });

  it('adds the ended notice only once when the session-ended signal repeats', async () => {
    const page = await newBoundPage();
    const callbacks = mockStartMessagePolling.mock.calls[0][1];

    callbacks.onSessionEnded();
    await page.waitForChanges();
    callbacks.onSessionEnded();
    await page.waitForChanges();

    const systemMessages = page.rootInstance.messages.filter((m: any) => m.role === 'system');
    expect(systemMessages).toHaveLength(1);
  });

  it('clears the ended state when the session is cleared', async () => {
    const page = await newBoundPage();
    const callbacks = mockStartMessagePolling.mock.calls[0][1];

    callbacks.onSessionEnded();
    await page.waitForChanges();
    expect(page.rootInstance.sessionEnded).toBe(true);

    await page.rootInstance.clearSession();
    await new Promise(resolve => setTimeout(resolve, 0));
    await page.waitForChanges();

    expect(page.rootInstance.sessionEnded).toBe(false);
  });

  it('preserves messages sent while the history is still loading', async () => {
    let resolveHistory: (messages: typeof history) => void;
    mockFetchAllMessages.mockReturnValueOnce(new Promise(resolve => (resolveHistory = resolve)));

    const page = await newBoundPage();

    // History fetch is still pending; user sends a message in the meantime.
    await page.rootInstance.sendMessage('Sent during load');
    resolveHistory([...history]);
    await new Promise(resolve => setTimeout(resolve, 0));
    await page.waitForChanges();

    const contents = page.rootInstance.messages.map((m: any) => m.content);
    expect(contents).toEqual(['Hi', 'Hello!', 'Sent during load']);
  });
});

describe('ocs-chat session tokens', () => {
  // NOTE: the module-level jest.mock('../../services/chat-session-service') factory at
  // the top of this file is inert under Stencil's jest preset (the factory never runs),
  // so the real ChatSessionService is always instantiated. These tests therefore observe
  // behaviour through the real service instance + the fetch/localStorage mocks, matching
  // the pattern used by the 'bound session' suite above.
  function tokenFetchMock() {
    const sessionToken = 'tok-1';
    return jest.fn().mockImplementation((url: string) => {
      if (url.includes('/api/chat/start/')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ session_id: 'test-session-id', session_token: sessionToken, chatbot: {}, participant: {} }),
        } as Response);
      }
      if (url.includes('/api/chat/send/')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ task_id: 'test-task-id', status: 'processing' }),
        } as Response);
      }
      return Promise.reject(new Error('Unexpected fetch call'));
    });
  }

  beforeEach(() => {
    jest.clearAllMocks();
    mockSendMessage.mockResolvedValue({ status: 'processing', task_id: 'test-task-id' });
    mockPollTask.mockReturnValue({ cancel: jest.fn() });
    mockStartMessagePolling.mockReturnValue({ stop: jest.fn() });
    mockFetchAllMessages.mockResolvedValue([]);
    global.fetch = tokenFetchMock();

    const store: Record<string, string> = {};
    Object.defineProperty(window, 'localStorage', {
      value: {
        getItem: jest.fn((k: string) => (k in store ? store[k] : null)),
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
    Object.defineProperty(window, 'crypto', {
      value: { getRandomValues: jest.fn((arr: Uint8Array) => arr) },
      writable: true,
    });
  });

  afterEach(async () => {
    await new Promise(resolve => setTimeout(resolve, 0));
    jest.restoreAllMocks();
  });

  it('requests a token on start and stores it on the service', async () => {
    const page = await newSpecPage({
      components: [OcsChat],
      html: '<open-chat-studio-widget chatbot-id="test-bot" visible="true"></open-chat-studio-widget>',
    });
    await page.waitForChanges();

    const svc = page.rootInstance['getChatService']();
    const setSessionTokenSpy = jest.spyOn(svc, 'setSessionToken');
    jest.spyOn(svc, 'startMessagePolling').mockImplementation(mockStartMessagePolling);
    jest.spyOn(svc, 'pollTask').mockImplementation(mockPollTask);

    await page.rootInstance.sendMessage('Hello');
    await page.waitForChanges();

    const startCall = (global.fetch as jest.Mock).mock.calls.find(call => call[0].includes('/api/chat/start/'));
    expect(startCall).toBeDefined();
    expect(setSessionTokenSpy).toHaveBeenCalledWith('tok-1');
    expect(window.localStorage.setItem).toHaveBeenCalledWith('ocs-chat-token-test-bot', 'tok-1');
  });

  it('restores a persisted token for an unbound session on load', async () => {
    (window.localStorage.getItem as jest.Mock).mockImplementation((key: string) => {
      if (key === 'ocs-chat-session-test-bot') return 'stored-session';
      if (key === 'ocs-chat-messages-test-bot') return JSON.stringify([]);
      if (key === 'ocs-chat-token-test-bot') return 'stored-tok';
      return null;
    });

    const page = await newSpecPage({
      components: [OcsChat],
      html: '<open-chat-studio-widget chatbot-id="test-bot" visible="false"></open-chat-studio-widget>',
    });
    await page.waitForChanges();

    expect(page.rootInstance.activeSessionId).toBe('stored-session');
    // The stored token is held on the component and handed to the service on creation.
    expect(page.rootInstance['currentSessionToken']).toBe('stored-tok');
    expect(page.rootInstance['getChatService']()['sessionToken']).toBe('stored-tok');
  });

  it('uses the session-token prop for a bound session and never persists it', async () => {
    const page = await newSpecPage({
      components: [OcsChat],
      html: '<open-chat-studio-widget chatbot-id="test-bot" session-id="host-session" session-token="host-tok" visible="false"></open-chat-studio-widget>',
    });
    await page.waitForChanges();

    expect(page.rootInstance.activeSessionId).toBe('host-session');
    expect(page.rootInstance['currentSessionToken']).toBe('host-tok');
    expect(page.rootInstance['getChatService']()['sessionToken']).toBe('host-tok');
    expect(window.localStorage.setItem).not.toHaveBeenCalledWith('ocs-chat-token-test-bot', expect.anything());
  });

  it('on an unbound 403 it shows a notice and resets the session for a fresh start', async () => {
    const page = await newSpecPage({
      components: [OcsChat],
      html: '<open-chat-studio-widget chatbot-id="test-bot" visible="true"></open-chat-studio-widget>',
    });
    await page.waitForChanges();

    // Start a session, then make the message send reject with a 403.
    await page.rootInstance.sendMessage('Hello');
    await page.waitForChanges();
    expect(page.rootInstance.activeSessionId).toBe('test-session-id');

    const svc = page.rootInstance['getChatService']();
    jest.spyOn(svc, 'sendMessage').mockRejectedValueOnce(new SessionAccessError(403, 'session_expired', 'Session has expired'));

    await page.rootInstance.sendMessage('again');
    await page.waitForChanges();

    // Session is discarded so the next send starts fresh, and a system notice is shown.
    expect(page.rootInstance.activeSessionId).toBeUndefined();
    expect(page.rootInstance['currentSessionToken']).toBeUndefined();
    const systemMessage = page.rootInstance.messages.find((m: any) => m.role === 'system');
    expect(systemMessage).toBeDefined();
  });

  it('dispatches ocs:session:started when a new session is created', async () => {
    // beforeEach configures mockStartSession to return session_id: 'test-session-id'
    const page = await newSpecPage({
      components: [OcsChat],
      html: '<open-chat-studio-widget chatbot-id="test-bot" visible="true"></open-chat-studio-widget>',
    });

    const dispatched: CustomEvent[] = [];
    page.root!.addEventListener('ocs:session:started', (e: Event) => dispatched.push(e as CustomEvent));

    await page.rootInstance.sendMessage('hello');
    await page.waitForChanges();

    expect(dispatched).toHaveLength(1);
    expect(dispatched[0].detail).toEqual({ sessionId: 'test-session-id' });
  });

  it('on a bound 403 it surfaces an error and stays bound', async () => {
    const page = await newSpecPage({
      components: [OcsChat],
      html: '<open-chat-studio-widget chatbot-id="test-bot" session-id="host-session" session-token="bad-tok" visible="true"></open-chat-studio-widget>',
    });

    const svc = page.rootInstance['getChatService']();
    jest.spyOn(svc, 'fetchAllMessages').mockRejectedValue(new SessionAccessError(403, 'session_token_invalid', 'Invalid session token'));

    // Trigger history load for the bound session.
    await page.rootInstance['loadBoundSessionHistory']();
    await page.waitForChanges();

    // Bound widget cannot restart: it stays on the host session and shows an error.
    expect(page.rootInstance.activeSessionId).toBe('host-session');
    const systemMessage = page.rootInstance.messages.find((m: any) => m.role === 'system');
    expect(systemMessage).toBeDefined();
  });
});

describe('ocs-chat kiosk restart after session end', () => {
  beforeEach(() => {
    jest.clearAllMocks();
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
    // The jest.mock('../../services/chat-session-service') factory above is inert
    // under Stencil's jest preset, so the real ChatSessionService is instantiated;
    // spy on it directly (mirrors the 'bound session' suite's newBoundPage helper).
    const svc = page.rootInstance['getChatService']();
    jest.spyOn(svc, 'startSession').mockImplementation(mockStartSession);
    jest.spyOn(svc, 'sendMessage').mockImplementation(mockSendMessage);
    jest.spyOn(svc, 'startMessagePolling').mockImplementation(mockStartMessagePolling);
    jest.spyOn(svc, 'pollTask').mockImplementation(mockPollTask);
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
    const svc = page.rootInstance['getChatService']();
    jest.spyOn(svc, 'fetchAllMessages').mockImplementation(mockFetchAllMessages);
    jest.spyOn(svc, 'startMessagePolling').mockImplementation(mockStartMessagePolling);
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
});

describe('ocs-chat visitor id', () => {
  const UUID_RE = /^ocs:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
  let store: Record<string, string>;

  beforeEach(() => {
    jest.clearAllMocks();
    store = {};
    Object.defineProperty(window, 'localStorage', {
      value: {
        getItem: jest.fn((k: string) => store[k] ?? null),
        setItem: jest.fn((k: string, v: string) => {
          store[k] = v;
        }),
        removeItem: jest.fn(),
        clear: jest.fn(),
      },
      writable: true,
    });
  });

  async function instance() {
    const page = await newSpecPage({
      components: [OcsChat],
      html: '<open-chat-studio-widget chatbot-id="test-bot"></open-chat-studio-widget>',
    });
    return page.rootInstance;
  }

  it('generates a v4 UUID with the ocs prefix and stores it', async () => {
    const id = (await instance())['getOrGenerateUserId']();
    expect(id).toMatch(UUID_RE);
    expect(store['ocs-user-id']).toBe(id);
  });

  it('reuses a stored id, including the legacy format', async () => {
    store['ocs-user-id'] = 'ocs:1700000000000_abc123def';
    expect((await instance())['getOrGenerateUserId']()).toBe('ocs:1700000000000_abc123def');
  });

  it('falls back to getRandomValues when randomUUID is unavailable', async () => {
    const original = window.crypto.randomUUID;
    Object.defineProperty(window.crypto, 'randomUUID', { value: undefined, configurable: true });
    try {
      expect((await instance())['getOrGenerateUserId']()).toMatch(UUID_RE);
    } finally {
      Object.defineProperty(window.crypto, 'randomUUID', { value: original, configurable: true });
    }
  });

  it('prefers the user-id prop over a generated id', async () => {
    const page = await newSpecPage({
      components: [OcsChat],
      html: '<open-chat-studio-widget chatbot-id="test-bot" user-id="me@example.com"></open-chat-studio-widget>',
    });
    expect(page.rootInstance['getOrGenerateUserId']()).toBe('me@example.com');
    expect(store['ocs-user-id']).toBeUndefined();
  });
});

describe('ocs-chat start payload timezone', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockStartSession.mockResolvedValue({ session_id: 'tz-session' });
    mockSendMessage.mockResolvedValue({ status: 'success', task_id: 'task' });
    Object.defineProperty(window, 'localStorage', {
      value: { getItem: jest.fn(() => null), setItem: jest.fn(), removeItem: jest.fn(), clear: jest.fn() },
      writable: true,
    });
    global.fetch = setupFetchMock('tz-session');
  });

  afterEach(() => jest.restoreAllMocks());

  it('sends the device time zone on start', async () => {
    jest.spyOn(Intl.DateTimeFormat.prototype, 'resolvedOptions').mockReturnValue({ timeZone: 'Africa/Johannesburg' } as Intl.ResolvedDateTimeFormatOptions);
    const page = await newSpecPage({
      components: [OcsChat],
      html: '<open-chat-studio-widget chatbot-id="test-bot" visible="true"></open-chat-studio-widget>',
    });
    const svc = page.rootInstance['getChatService']();
    jest.spyOn(svc, 'startSession').mockImplementation(mockStartSession);
    jest.spyOn(svc, 'sendMessage').mockImplementation(mockSendMessage);
    jest.spyOn(svc, 'startMessagePolling').mockImplementation(mockStartMessagePolling);
    jest.spyOn(svc, 'pollTask').mockImplementation(mockPollTask);
    await page.rootInstance.sendMessage('hello');

    expect(mockStartSession).toHaveBeenCalledWith(expect.objectContaining({ timezone: 'Africa/Johannesburg' }));
  });

  it('omits the time zone when the browser cannot resolve one', async () => {
    jest.spyOn(Intl.DateTimeFormat.prototype, 'resolvedOptions').mockReturnValue({} as Intl.ResolvedDateTimeFormatOptions);
    const page = await newSpecPage({
      components: [OcsChat],
      html: '<open-chat-studio-widget chatbot-id="test-bot" visible="true"></open-chat-studio-widget>',
    });
    const svc = page.rootInstance['getChatService']();
    jest.spyOn(svc, 'startSession').mockImplementation(mockStartSession);
    jest.spyOn(svc, 'sendMessage').mockImplementation(mockSendMessage);
    jest.spyOn(svc, 'startMessagePolling').mockImplementation(mockStartMessagePolling);
    jest.spyOn(svc, 'pollTask').mockImplementation(mockPollTask);
    await page.rootInstance.sendMessage('hello');

    expect(mockStartSession.mock.calls[0][0]).not.toHaveProperty('timezone');
  });
});
