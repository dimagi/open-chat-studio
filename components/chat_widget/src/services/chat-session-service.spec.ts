import { ChatSessionService } from './chat-session-service';

function progressMessage(content: string) {
  return {
    status: 'processing' as const,
    message: {
      created_at: new Date().toISOString(),
      role: 'assistant' as const,
      content,
      metadata: {},
      attachments: [],
    },
  };
}

function completeMessage(content: string) {
  return {
    status: 'complete' as const,
    message: {
      created_at: new Date().toISOString(),
      role: 'assistant' as const,
      content,
      attachments: [],
    },
  };
}

describe('ChatSessionService.pollTask', () => {
  let service: ChatSessionService;

  beforeEach(() => {
    service = new ChatSessionService({
      apiBaseUrl: 'https://example.com',
      widgetVersion: '1.0.0',
      taskPollingIntervalMs: 10,
      taskPollingMaxAttempts: 5,
    });
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('should call onProgress with message content when status is processing', async () => {
    const onMessage = jest.fn();
    const onProgress = jest.fn();

    let pollCount = 0;
    jest.spyOn(service, 'pollTaskOnce').mockImplementation(async () => {
      pollCount++;
      if (pollCount === 1) {
        return progressMessage('Searching...');
      }
      return completeMessage('Done');
    });

    const handle = service.pollTask('session-1', 'task-1', {
      onMessage,
      onProgress,
    });

    // Wait for both poll cycles to complete
    await new Promise(resolve => setTimeout(resolve, 50));
    handle.cancel();

    expect(onProgress).toHaveBeenCalledWith('Searching...');
    expect(onMessage).toHaveBeenCalledTimes(1);
    expect(onMessage.mock.calls[0][0].content).toBe('Done');
  });

  it('should not call onProgress when processing response has no message', async () => {
    const onMessage = jest.fn();
    const onProgress = jest.fn();

    let pollCount = 0;
    jest.spyOn(service, 'pollTaskOnce').mockImplementation(async () => {
      pollCount++;
      if (pollCount === 1) {
        return { status: 'processing' };
      }
      return completeMessage('Done');
    });

    const handle = service.pollTask('session-1', 'task-1', {
      onMessage,
      onProgress,
    });

    await new Promise(resolve => setTimeout(resolve, 50));
    handle.cancel();

    expect(onProgress).not.toHaveBeenCalled();
    expect(onMessage).toHaveBeenCalledTimes(1);
  });

  it('should call onProgress multiple times as progress updates arrive', async () => {
    const onMessage = jest.fn();
    const onProgress = jest.fn();

    let pollCount = 0;
    jest.spyOn(service, 'pollTaskOnce').mockImplementation(async () => {
      pollCount++;
      if (pollCount === 1) {
        return progressMessage('Step 1');
      }
      if (pollCount === 2) {
        return progressMessage('Step 2');
      }
      return completeMessage('Done');
    });

    const handle = service.pollTask('session-1', 'task-1', {
      onMessage,
      onProgress,
    });

    await new Promise(resolve => setTimeout(resolve, 100));
    handle.cancel();

    expect(onProgress).toHaveBeenCalledTimes(2);
    expect(onProgress).toHaveBeenNthCalledWith(1, 'Step 1');
    expect(onProgress).toHaveBeenNthCalledWith(2, 'Step 2');
    expect(onMessage).toHaveBeenCalledTimes(1);
  });

  it('should not call onProgress when processing message has empty content', async () => {
    const onMessage = jest.fn();
    const onProgress = jest.fn();

    let pollCount = 0;
    jest.spyOn(service, 'pollTaskOnce').mockImplementation(async () => {
      pollCount++;
      if (pollCount === 1) {
        return progressMessage('');
      }
      return completeMessage('Done');
    });

    const handle = service.pollTask('session-1', 'task-1', {
      onMessage,
      onProgress,
    });

    await new Promise(resolve => setTimeout(resolve, 50));
    handle.cancel();

    expect(onProgress).not.toHaveBeenCalled();
    expect(onMessage).toHaveBeenCalledTimes(1);
  });
});

describe('ChatSessionService.fetchAllMessages', () => {
  let service: ChatSessionService;

  function historyMessage(content: string, createdAt: string) {
    return {
      created_at: createdAt,
      role: 'assistant' as const,
      content,
      attachments: [],
    };
  }

  beforeEach(() => {
    service = new ChatSessionService({
      apiBaseUrl: 'https://example.com',
      widgetVersion: '1.0.0',
    });
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('returns messages from a single page', async () => {
    const fetchMock = jest.spyOn(service, 'fetchMessages').mockResolvedValue({
      messages: [historyMessage('a', '2026-01-01T00:00:01Z')],
      has_more: false,
      session_status: 'active',
    });

    const result = await service.fetchAllMessages('session-1');

    expect(result.map(m => m.content)).toEqual(['a']);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith('session-1', undefined);
  });

  it('pages through history until has_more is false', async () => {
    const fetchMock = jest
      .spyOn(service, 'fetchMessages')
      .mockResolvedValueOnce({
        messages: [historyMessage('a', '2026-01-01T00:00:01Z'), historyMessage('b', '2026-01-01T00:00:02Z')],
        has_more: true,
        session_status: 'active',
      })
      .mockResolvedValueOnce({
        messages: [historyMessage('c', '2026-01-01T00:00:03Z')],
        has_more: false,
        session_status: 'active',
      });

    const result = await service.fetchAllMessages('session-1');

    expect(result.map(m => m.content)).toEqual(['a', 'b', 'c']);
    expect(fetchMock).toHaveBeenNthCalledWith(1, 'session-1', undefined);
    expect(fetchMock).toHaveBeenNthCalledWith(2, 'session-1', '2026-01-01T00:00:02Z');
  });

  it('stops paging if the server keeps reporting has_more with empty pages', async () => {
    const fetchMock = jest.spyOn(service, 'fetchMessages').mockResolvedValue({
      messages: [],
      has_more: true,
      session_status: 'active',
    });

    const result = await service.fetchAllMessages('session-1');

    expect(result).toEqual([]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
