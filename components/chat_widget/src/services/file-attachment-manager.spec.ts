import { FileAttachmentManager } from './file-attachment-manager';

function makeManager() {
  return new FileAttachmentManager({ supportedExtensions: ['.txt'], maxFileSizeMb: 50, maxTotalSizeMb: 50 });
}

function makeFile(name = 'a.txt') {
  return new File(['hello'], name, { type: 'text/plain' });
}

describe('FileAttachmentManager session tokens', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('sends the X-Session-Token header on upload when provided', async () => {
    const manager = makeManager();
    const fetchMock = jest.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      status: 201,
      json: () => Promise.resolve({ files: [{ id: 1, name: 'a.txt', size: 5, content_type: 'text/plain' }] }),
    } as Response);

    await manager.uploadPendingFiles([{ file: makeFile() }], {
      apiBaseUrl: 'https://example.com',
      sessionId: 's1',
      participantId: 'p1',
      sessionToken: 'tok-123',
    });

    const headers = (fetchMock.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect(headers['X-Session-Token']).toBe('tok-123');
  });

  it('flags tokenRejected on a 403 upload response', async () => {
    const manager = makeManager();
    jest.spyOn(global, 'fetch').mockResolvedValue({
      ok: false,
      status: 403,
      json: () => Promise.resolve({ error: 'Session token required', code: 'session_token_required' }),
    } as Response);

    const result = await manager.uploadPendingFiles([{ file: makeFile() }], {
      apiBaseUrl: 'https://example.com',
      sessionId: 's1',
      participantId: 'p1',
      sessionToken: 'tok-123',
    });

    expect(result.tokenRejected).toBe(true);
    expect(result.errorMessage).toBe('Session token required');
  });

  it('omits the X-Session-Token header when no token is provided', async () => {
    const manager = makeManager();
    const fetchMock = jest.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      status: 201,
      json: () => Promise.resolve({ files: [{ id: 1, name: 'a.txt', size: 5, content_type: 'text/plain' }] }),
    } as Response);

    await manager.uploadPendingFiles([{ file: makeFile() }], {
      apiBaseUrl: 'https://example.com',
      sessionId: 's1',
      participantId: 'p1',
    });

    const headers = (fetchMock.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect(headers['X-Session-Token']).toBeUndefined();
  });

  it('does not flag tokenRejected on a non-403 failure', async () => {
    const manager = makeManager();
    jest.spyOn(global, 'fetch').mockResolvedValue({
      ok: false,
      status: 500,
      json: () => Promise.resolve({ error: 'boom' }),
    } as Response);

    const result = await manager.uploadPendingFiles([{ file: makeFile() }], {
      apiBaseUrl: 'https://example.com',
      sessionId: 's1',
      participantId: 'p1',
    });

    expect(result.tokenRejected).toBeFalsy();
  });
});
