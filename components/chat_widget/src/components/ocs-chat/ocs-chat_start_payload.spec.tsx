import { newSpecPage } from '@stencil/core/testing';
import { OcsChat } from './ocs-chat';
import { installWebCrypto, setupFetchMock, stubChatService } from './ocs-chat.test-helpers';

// Matches the `ocs:` visitor id prefix followed by a v4 UUID.
const UUID_RE = /^ocs:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

const mockStartSession = jest.fn();
const mockSendMessage = jest.fn();
const mockPollTask = jest.fn();
const mockStartMessagePolling = jest.fn();

describe('ocs-chat visitor id', () => {
  let store: Record<string, string>;

  beforeEach(() => {
    jest.clearAllMocks();
    installWebCrypto();
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
    installWebCrypto();
    mockStartSession.mockResolvedValue({ session_id: 'tz-session' });
    mockSendMessage.mockResolvedValue({ status: 'success', task_id: 'task' });
    Object.defineProperty(window, 'localStorage', {
      value: { getItem: jest.fn(() => null), setItem: jest.fn(), removeItem: jest.fn(), clear: jest.fn() },
      writable: true,
    });
    global.fetch = setupFetchMock('tz-session');
  });

  afterEach(async () => {
    await new Promise(resolve => setTimeout(resolve, 0));
    jest.restoreAllMocks();
  });

  async function newStartedPage() {
    const page = await newSpecPage({
      components: [OcsChat],
      html: '<open-chat-studio-widget chatbot-id="test-bot" visible="true"></open-chat-studio-widget>',
    });
    stubChatService(page, {
      startSession: mockStartSession,
      sendMessage: mockSendMessage,
      startMessagePolling: mockStartMessagePolling,
      pollTask: mockPollTask,
    });
    await page.rootInstance.sendMessage('hello');
    return page;
  }

  it('sends the device time zone on start', async () => {
    jest.spyOn(Intl.DateTimeFormat.prototype, 'resolvedOptions').mockReturnValue({ timeZone: 'Africa/Johannesburg' } as Intl.ResolvedDateTimeFormatOptions);
    await newStartedPage();

    expect(mockStartSession).toHaveBeenCalledWith(expect.objectContaining({ timezone: 'Africa/Johannesburg' }));
  });

  it('omits the time zone when the browser cannot resolve one', async () => {
    jest.spyOn(Intl.DateTimeFormat.prototype, 'resolvedOptions').mockReturnValue({} as Intl.ResolvedDateTimeFormatOptions);
    await newStartedPage();

    expect(mockStartSession.mock.calls[0][0]).not.toHaveProperty('timezone');
  });
});
