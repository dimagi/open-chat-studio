import type { Mock } from 'vitest';
import { webcrypto } from 'crypto';
import type { ChatSessionService } from '../../services/chat-session-service';

// The spec DOM has no window.crypto; the widget needs getRandomValues (and
// prefers randomUUID) to mint a visitor id before it can start a session.
export function installWebCrypto(): void {
  Object.defineProperty(window, 'crypto', { value: webcrypto, writable: true, configurable: true });
}

type ChatServiceStubs = Partial<Record<keyof ChatSessionService, Mock>>;

// Under stencilVitestPlugin a component compiles to a custom element, so the element is
// also the component instance and private members are reachable on it.
export interface SpecPage {
  root: HTMLElement & Record<string, any>;
  rootInstance: any;
  body: HTMLElement;
  doc: Document;
  waitForChanges: () => Promise<void>;
}

// Lets Stencil's requestAnimationFrame-batched render cycle finish for `root`.
async function waitForChanges(root: (HTMLElement & { componentOnReady?: () => Promise<unknown> }) | null): Promise<void> {
  for (let i = 0; i < 2; i++) {
    await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
  }
  await root?.componentOnReady?.();
}

// Mounts `html` into a clean document, in the shape of Stencil's former newSpecPage(). Like the
// original it returns before timers fire, so tests can stub the service ahead of the widget's
// deferred start-up work; call `waitForChanges()` to let rendering settle.
export async function newSpecPage({ html }: { components?: unknown[]; html: string }): Promise<SpecPage> {
  document.body.innerHTML = html;
  const root = document.body.firstElementChild as SpecPage['root'] | null;
  if (!root) {
    throw new Error('newSpecPage: the html produced no root element');
  }
  return { root, rootInstance: root, body: document.body, doc: document, waitForChanges: () => waitForChanges(root) };
}

// Tests spy on the widget's ChatSessionService instance rather than mocking the module.
export function stubChatService(page: SpecPage, stubs: ChatServiceStubs): void {
  const svc = page.rootInstance['getChatService']();
  for (const [method, impl] of Object.entries(stubs)) {
    vi.spyOn(svc, method as keyof ChatSessionService).mockImplementation(impl);
  }
}

// Helper to create fetch mock with configurable session ID
export function setupFetchMock(sessionId = 'test-session-id', taskId = 'test-task-id'): Mock {
  return vi.fn().mockImplementation((url: string) => {
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
