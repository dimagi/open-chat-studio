import { newSpecPage, SpecPage } from '@stencil/core/testing';
import { OcsChat } from './ocs-chat';
import { installWebCrypto, stubChatService } from './ocs-chat.test-helpers';
import { ConsentRequiredError } from '../../services/chat-session-service';

const consentBlock = { required: true, form_version_id: 7, text: '<p>Please <strong>agree</strong></p>' };
const consented = { required: false, form_version_id: 7, text: null };

async function mountWidget(attrs = ''): Promise<SpecPage> {
  installWebCrypto();
  const page = await newSpecPage({
    components: [OcsChat],
    html: `<open-chat-studio-widget chatbot-id="bot" api-base-url="http://x" visible="true" ${attrs}></open-chat-studio-widget>`,
  });
  await page.waitForChanges();
  return page;
}

function startResponse(consent = consentBlock) {
  return Promise.resolve({ session_id: 'session-1', session_token: 'tok', chatbot: {}, participant: {}, consent });
}

function processing() {
  return jest.fn().mockResolvedValue({ task_id: 't', status: 'processing' });
}

function stubService(page: SpecPage, overrides: Record<string, jest.Mock> = {}) {
  stubChatService(page, {
    startSession: jest.fn(() => startResponse()),
    recordConsent: jest.fn().mockResolvedValue(undefined),
    sendMessage: jest.fn(),
    startMessagePolling: jest.fn().mockReturnValue({ stop: jest.fn() }),
    pollTask: jest.fn().mockReturnValue({ cancel: jest.fn() }),
    ...overrides,
  });
}

function consentPanel(page: SpecPage) {
  return page.root.shadowRoot.querySelector('.consent-panel');
}

async function settle(page: SpecPage) {
  await page.waitForChanges();
  await new Promise(process.nextTick);
  await page.waitForChanges();
}

describe('consent memory', () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('remembers an accepted form version under the persistence store', async () => {
    const page = await mountWidget('persistent-session="true"');
    const recordConsent = jest.fn().mockResolvedValue(undefined);
    stubService(page, { recordConsent, sendMessage: processing() });

    await page.rootInstance['sendMessage']('hello');
    await page.waitForChanges();
    await page.rootInstance['acceptConsent']();
    await settle(page);

    expect(recordConsent).toHaveBeenCalledWith('session-1', 7);
    expect(window.localStorage.getItem('ocs-chat-consent-bot')).toBe('7');
  });

  it('posts consent silently when the stored form version matches', async () => {
    const page = await mountWidget('persistent-session="true"');
    window.localStorage.setItem('ocs-chat-consent-bot', '7');
    const recordConsent = jest.fn().mockResolvedValue(undefined);
    const sendMessage = processing();
    stubService(page, { recordConsent, sendMessage });

    await page.rootInstance['sendMessage']('hello');
    await settle(page);

    expect(recordConsent).toHaveBeenCalledWith('session-1', 7);
    expect(sendMessage).toHaveBeenCalledTimes(1);
    expect(consentPanel(page)).toBeNull();
  });

  it('re-prompts when the stored form version is stale', async () => {
    const page = await mountWidget('persistent-session="true"');
    window.localStorage.setItem('ocs-chat-consent-bot', '6');
    const sendMessage = jest.fn();
    stubService(page, { sendMessage });

    await page.rootInstance['sendMessage']('hello');
    await settle(page);

    expect(sendMessage).not.toHaveBeenCalled();
    expect(consentPanel(page)).not.toBeNull();
  });

  it('forgets a stored acceptance the server calls stale', async () => {
    const page = await mountWidget('persistent-session="true"');
    window.localStorage.setItem('ocs-chat-consent-bot', '7');
    const current = { ...consentBlock, form_version_id: 8 };
    stubService(page, {
      recordConsent: jest.fn().mockRejectedValue(new ConsentRequiredError(current, 'The consent form has changed')),
    });

    await page.rootInstance['sendMessage']('hello');
    await settle(page);

    expect(window.localStorage.getItem('ocs-chat-consent-bot')).toBeNull();
    expect(consentPanel(page)).not.toBeNull();
    expect(page.rootInstance['consent']).toEqual(current);
  });

  it('asks on every visit when persistence is off', async () => {
    const page = await mountWidget('persistent-session="false"');
    window.localStorage.setItem('ocs-chat-consent-bot', '7');
    const sendMessage = jest.fn();
    stubService(page, { sendMessage });

    await page.rootInstance['sendMessage']('hello');
    await settle(page);

    expect(sendMessage).not.toHaveBeenCalled();
    expect(consentPanel(page)).not.toBeNull();
  });

  it('keeps the stored acceptance when the session is cleared', async () => {
    const page = await mountWidget('persistent-session="true"');
    window.localStorage.setItem('ocs-chat-consent-bot', '7');
    stubService(page, { sendMessage: processing() });

    await page.rootInstance['sendMessage']('hello');
    await settle(page);
    await page.rootInstance['clearSession']();
    await page.waitForChanges();

    expect(window.localStorage.getItem('ocs-chat-consent-bot')).toBe('7');
    expect(page.rootInstance['consent']).toBeUndefined();
  });

  it('falls back to asking when a silent consent post fails, without retrying or reporting', async () => {
    const page = await mountWidget('persistent-session="true"');
    window.localStorage.setItem('ocs-chat-consent-bot', '7');
    const recordConsent = jest.fn().mockRejectedValue(new Error('boom'));
    const startMessagePolling = jest.fn().mockReturnValue({ stop: jest.fn() });
    stubService(page, { recordConsent, startMessagePolling });

    await page.rootInstance['sendMessage']('hello');
    await settle(page);
    const callbacks = startMessagePolling.mock.calls[0][1];
    callbacks.onConsent(consentBlock);
    await settle(page);

    expect(recordConsent).toHaveBeenCalledTimes(1);
    expect(page.rootInstance['messages'].filter((m: { role: string }) => m.role === 'system')).toHaveLength(0);
    expect(consentPanel(page)).not.toBeNull();
  });

  it('takes a consent block delivered by polling', async () => {
    const page = await mountWidget('persistent-session="true"');
    const startMessagePolling = jest.fn().mockReturnValue({ stop: jest.fn() });
    stubService(page, {
      startSession: jest.fn(() => startResponse(consented)),
      sendMessage: processing(),
      startMessagePolling,
    });

    await page.rootInstance['sendMessage']('hello');
    await settle(page);
    const callbacks = startMessagePolling.mock.calls[0][1];
    callbacks.onConsent(consentBlock);
    await settle(page);

    expect(page.rootInstance['consent']).toEqual(consentBlock);
  });
});

describe('hold and release', () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('holds the first message and shows the panel when consent is required', async () => {
    const page = await mountWidget();
    const sendMessage = jest.fn();
    stubService(page, { sendMessage });

    await page.rootInstance['sendMessage']('hello');
    await settle(page);

    expect(sendMessage).not.toHaveBeenCalled();
    expect(page.rootInstance['heldMessage']).toBe('hello');
    expect(page.root.shadowRoot.querySelector('.consent-panel .consent-text').innerHTML).toContain('<strong>agree</strong>');
    expect(page.root.shadowRoot.querySelector('.message-textarea')).toBeNull();
  });

  it('accepting posts consent, then sends the held message', async () => {
    const page = await mountWidget();
    const recordConsent = jest.fn().mockResolvedValue(undefined);
    const sendMessage = processing();
    stubService(page, { recordConsent, sendMessage });

    await page.rootInstance['sendMessage']('hello');
    await settle(page);
    (page.root.shadowRoot.querySelector('.consent-agree') as HTMLButtonElement).click();
    await settle(page);

    expect(recordConsent).toHaveBeenCalledWith('session-1', 7);
    expect(sendMessage).toHaveBeenCalledWith('session-1', expect.objectContaining({ message: 'hello' }));
    expect(consentPanel(page)).toBeNull();
    expect(page.root.shadowRoot.querySelector('.message-textarea')).not.toBeNull();
  });

  it('disables the agree button while the acceptance is in flight', async () => {
    const page = await mountWidget();
    let releasePost: () => void;
    const recordConsent = jest.fn(() => new Promise<void>(resolve => (releasePost = resolve)));
    stubService(page, { recordConsent, sendMessage: processing() });

    await page.rootInstance['sendMessage']('hello');
    await settle(page);
    (page.root.shadowRoot.querySelector('.consent-agree') as HTMLButtonElement).click();
    await page.waitForChanges();

    expect(page.root.shadowRoot.querySelector('.consent-agree').hasAttribute('disabled')).toBe(true);

    releasePost();
    await settle(page);
  });

  it('a consent refusal on send re-opens the panel without discarding the session', async () => {
    const page = await mountWidget();
    const sendMessage = jest.fn().mockRejectedValueOnce(new ConsentRequiredError(consentBlock, 'Consent is required'));
    stubService(page, { startSession: jest.fn(() => startResponse(consented)), sendMessage });

    await page.rootInstance['sendMessage']('hello');
    await settle(page);

    expect(page.rootInstance['activeSessionId']).toBe('session-1');
    expect(page.rootInstance['heldMessage']).toBe('hello');
    expect(consentPanel(page)).not.toBeNull();
    expect(page.rootInstance['messages'].filter((m: { role: string }) => m.role === 'user')).toHaveLength(0);
  });

  it('a consent refusal on upload holds the message and keeps the files', async () => {
    const page = await mountWidget('allow-attachments="true"');
    const sendMessage = jest.fn();
    stubService(page, { startSession: jest.fn(() => startResponse(consented)), sendMessage });
    const file = new File(['hello'], 'a.txt', { type: 'text/plain' });
    page.rootInstance['selectedFiles'] = [{ file }];
    jest.spyOn(page.rootInstance['attachmentManager'], 'uploadPendingFiles').mockResolvedValue({
      selectedFiles: [{ file }],
      uploadedIds: [],
      errorMessage: 'Consent is required',
      tokenRejected: false,
      consent: consentBlock,
    });

    await page.rootInstance['sendMessage']('hello');
    await settle(page);

    expect(sendMessage).not.toHaveBeenCalled();
    expect(page.rootInstance['heldMessage']).toBe('hello');
    expect(page.rootInstance['selectedFiles']).toHaveLength(1);
    expect(consentPanel(page)).not.toBeNull();
  });

  it('does not send the message when an upload refusal names consent but carries no block', async () => {
    const page = await mountWidget('allow-attachments="true"');
    const sendMessage = jest.fn().mockResolvedValue({ task_id: 't', status: 'processing' });
    stubService(page, { startSession: jest.fn(() => startResponse(consented)), sendMessage });
    page.rootInstance['selectedFiles'] = [{ file: new File(['hello'], 'a.txt', { type: 'text/plain' }) }];
    // The real attachment manager, driven by a refusal whose body has no consent block.
    jest.spyOn(global, 'fetch').mockResolvedValue({
      ok: false,
      status: 403,
      json: () => Promise.resolve({ error: 'Consent is required', code: 'consent_required' }),
    } as Response);

    await page.rootInstance['sendMessage']('hello');
    await settle(page);

    expect(sendMessage).not.toHaveBeenCalled();
  });

  it('welcome messages and starter questions render before consent', async () => {
    const page = await mountWidget(`welcome-messages='["Hi there"]' starter-questions='["What can you do?"]'`);
    stubService(page);

    expect(page.root.shadowRoot.textContent).toContain('Hi there');
    expect(page.root.shadowRoot.textContent).toContain('What can you do?');
  });
});
