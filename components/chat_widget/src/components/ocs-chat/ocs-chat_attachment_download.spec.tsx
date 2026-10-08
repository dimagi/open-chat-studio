import { newSpecPage, SpecPage } from '@stencil/core/testing';
import { OcsChat } from './ocs-chat';
import { stubChatService } from './ocs-chat.test-helpers';
import type { ChatAttachment } from '../../services/chat-session-service';

const DOWNLOAD_URL = 'https://example.com/api/chat/session-1/files/7/content/';

async function renderWithAttachment(attachment: ChatAttachment): Promise<SpecPage> {
  const page = await newSpecPage({
    components: [OcsChat],
    html: `<open-chat-studio-widget chatbot-id="test-bot" visible="true"></open-chat-studio-widget>`,
  });
  const component = page.rootInstance as OcsChat;
  component.activeSessionId = 'session-1';
  component.messages = [{ created_at: new Date().toISOString(), role: 'assistant', content: 'Here is your file', attachments: [attachment] }];
  await page.waitForChanges();
  return page;
}

const downloadButton = (page: SpecPage) => page.root?.shadowRoot?.querySelector('button.message-attachment-link') as HTMLButtonElement | null;

describe('ocs-chat attachment download', () => {
  let createObjectURL: jest.Mock;
  let revokeObjectURL: jest.Mock;
  let clickedLinks: HTMLAnchorElement[];

  beforeEach(() => {
    createObjectURL = jest.fn(() => 'blob:file-1');
    revokeObjectURL = jest.fn();
    Object.assign(URL, { createObjectURL, revokeObjectURL });
    clickedLinks = [];
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  function captureDownloadLinks(page: SpecPage) {
    const body = page.doc.body;
    const appendChild = body.appendChild.bind(body);
    jest.spyOn(body, 'appendChild').mockImplementation(<T extends Node>(node: T): T => {
      const link = node as unknown as HTMLAnchorElement;
      link.click = () => {
        clickedLinks.push(link);
      };
      return appendChild(node);
    });
  }

  it('renders the name as plain text when the attachment has no download URL', async () => {
    const page = await renderWithAttachment({ name: 'report.pdf', content_type: 'application/pdf', size: 3 });

    expect(downloadButton(page)).toBeNull();
    expect(page.root?.shadowRoot?.querySelector('.message-attachment-name')?.textContent).toBe('report.pdf');
  });

  it('renders the name as a download button when the attachment has a download URL', async () => {
    const page = await renderWithAttachment({ name: 'report.pdf', content_type: 'application/pdf', size: 3, download_url: DOWNLOAD_URL });

    const button = downloadButton(page);
    expect(button?.textContent).toBe('report.pdf');
    expect(button?.getAttribute('aria-label')).toBe('Download file: report.pdf');
  });

  it('downloads the file through the chat service and saves it under the attachment name', async () => {
    const page = await renderWithAttachment({ name: 'report.pdf', content_type: 'application/pdf', size: 3, download_url: DOWNLOAD_URL });
    const blob = { size: 3 } as Blob;
    const downloadAttachment = jest.fn().mockResolvedValue(blob);
    stubChatService(page, { downloadAttachment });
    captureDownloadLinks(page);

    downloadButton(page)?.click();
    await page.waitForChanges();
    await new Promise(resolve => setTimeout(resolve, 0));

    expect(downloadAttachment).toHaveBeenCalledWith('session-1', DOWNLOAD_URL);
    expect(createObjectURL).toHaveBeenCalledWith(blob);
    expect(clickedLinks).toHaveLength(1);
    expect(clickedLinks[0].getAttribute('href')).toBe('blob:file-1');
    expect(clickedLinks[0].download).toBe('report.pdf');
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:file-1');
  });

  it('reports a failed download in the chat without ending the session', async () => {
    const page = await renderWithAttachment({ name: 'report.pdf', content_type: 'application/pdf', size: 3, download_url: DOWNLOAD_URL });
    stubChatService(page, { downloadAttachment: jest.fn().mockRejectedValue(new Error('Failed to download file: Not Found')) });
    jest.spyOn(console, 'error').mockImplementation(() => undefined);

    downloadButton(page)?.click();
    await page.waitForChanges();
    await new Promise(resolve => setTimeout(resolve, 0));

    const component = page.rootInstance as OcsChat;
    expect(component.messages.at(-1)?.content).toContain('Could not download the file.');
    expect(component.activeSessionId).toBe('session-1');
    expect(createObjectURL).not.toHaveBeenCalled();
    expect(component.downloadingAttachmentUrls).toEqual([]);
  });

  it('keeps the error notice out of the message polling cursor', async () => {
    const page = await renderWithAttachment({ name: 'report.pdf', content_type: 'application/pdf', size: 3, download_url: DOWNLOAD_URL });
    const component = page.rootInstance as OcsChat;
    const serverMessageAt = '2026-01-01T00:00:00.000Z';
    component.messages = [{ ...component.messages[0], created_at: serverMessageAt }];
    let getSince: (() => string | undefined) | undefined;
    stubChatService(page, {
      downloadAttachment: jest.fn().mockRejectedValue(new Error('Failed to download file: Not Found')),
      startMessagePolling: jest.fn((_sessionId, callbacks) => {
        getSince = callbacks.getSince;
        return { stop: jest.fn() };
      }),
    });
    jest.spyOn(console, 'error').mockImplementation(() => undefined);

    downloadButton(page)?.click();
    await page.waitForChanges();
    await new Promise(resolve => setTimeout(resolve, 0));
    component['messagePollingHandle'] = undefined;
    component['startMessagePolling']();

    expect(component.messages.at(-1)?.local).toBe(true);
    expect(getSince?.()).toBe(serverMessageAt);
  });

  it('ignores a second click while the file is downloading', async () => {
    const page = await renderWithAttachment({ name: 'report.pdf', content_type: 'application/pdf', size: 3, download_url: DOWNLOAD_URL });
    const downloadAttachment = jest.fn(() => new Promise<Blob>(() => undefined));
    stubChatService(page, { downloadAttachment });

    downloadButton(page)?.click();
    await page.waitForChanges();
    const component = page.rootInstance as OcsChat;
    await component['downloadAttachment']({ name: 'report.pdf', content_type: 'application/pdf', size: 3, download_url: DOWNLOAD_URL });

    expect(downloadAttachment).toHaveBeenCalledTimes(1);
    expect(downloadButton(page)?.hasAttribute('disabled')).toBe(true);
  });
});
