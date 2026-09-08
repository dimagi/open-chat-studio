import { webcrypto } from 'crypto';
import type { SpecPage } from '@stencil/core/testing';
import type { ChatSessionService } from '../../services/chat-session-service';

// The spec DOM has no window.crypto; the widget needs getRandomValues (and
// prefers randomUUID) to mint a visitor id before it can start a session.
export function installWebCrypto(): void {
  Object.defineProperty(window, 'crypto', { value: webcrypto, writable: true, configurable: true });
}

type ChatServiceStubs = Partial<Record<keyof ChatSessionService, jest.Mock>>;

// The widget instantiates a real ChatSessionService (a jest.mock factory for the
// module is inert under Stencil's jest preset), so tests spy on the instance.
export function stubChatService(page: SpecPage, stubs: ChatServiceStubs): void {
  const svc = page.rootInstance['getChatService']();
  for (const [method, impl] of Object.entries(stubs)) {
    jest.spyOn(svc, method as keyof ChatSessionService).mockImplementation(impl);
  }
}

// Helper to create fetch mock with configurable session ID
export function setupFetchMock(sessionId = 'test-session-id', taskId = 'test-task-id') {
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
