import { FileAttachmentManager } from './file-attachment-manager';

function makeManager() {
  return new FileAttachmentManager({ supportedExtensions: ['.txt'], maxFileSizeMb: 50, maxTotalSizeMb: 50 });
}

function makeFile(name = 'a.txt') {
  return new File(['hello'], name, { type: 'text/plain' });
}

describe('FileAttachmentManager upload transport', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('sends through the provided transport with the upload url and form data', async () => {
    const manager = makeManager();
    const send = jest.fn().mockResolvedValue({
      ok: true,
      status: 201,
      json: () => Promise.resolve({ files: [{ id: 1, name: 'a.txt', size: 5, content_type: 'text/plain' }] }),
    } as Response);

    const result = await manager.uploadPendingFiles([{ file: makeFile() }], {
      apiBaseUrl: 'https://example.com',
      sessionId: 's1',
      participantId: 'p1',
      send,
    });

    expect(send).toHaveBeenCalledWith('https://example.com/api/chat/s1/upload/', expect.any(FormData));
    expect(result.uploadedIds).toEqual([1]);
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
    });

    expect(result.tokenRejected).toBe(true);
    expect(result.errorMessage).toBe('Session token required');
  });

  it('reports a consent refusal without treating it as a token rejection', async () => {
    const manager = makeManager();
    const consentBlock = { required: true, form_version_id: 7, text: '<p>Please agree</p>' };
    jest.spyOn(global, 'fetch').mockResolvedValue({
      ok: false,
      status: 403,
      json: () => Promise.resolve({ error: 'Consent is required', code: 'consent_required', consent: consentBlock }),
    } as Response);

    const result = await manager.uploadPendingFiles([{ file: makeFile() }], {
      apiBaseUrl: 'https://example.com',
      sessionId: 's1',
      participantId: 'p1',
    });

    expect(result.tokenRejected).toBe(false);
    expect(result.consent).toEqual(consentBlock);
    expect(result.selectedFiles[0].error).toBeUndefined();
  });

  it('treats a consent refusal with no block as an ordinary upload failure', async () => {
    const manager = makeManager();
    jest.spyOn(global, 'fetch').mockResolvedValue({
      ok: false,
      status: 403,
      json: () => Promise.resolve({ error: 'Consent is required', code: 'consent_required' }),
    } as Response);

    const result = await manager.uploadPendingFiles([{ file: makeFile() }], {
      apiBaseUrl: 'https://example.com',
      sessionId: 's1',
      participantId: 'p1',
    });

    // Not a token rejection: the file error stops the send instead.
    expect(result.consent).toBeUndefined();
    expect(result.tokenRejected).toBe(false);
    expect(result.selectedFiles[0].error).toBe('Consent is required');
  });

  it('posts with fetch and no auth headers when no transport is provided', async () => {
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
    expect(headers).toEqual({});
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
