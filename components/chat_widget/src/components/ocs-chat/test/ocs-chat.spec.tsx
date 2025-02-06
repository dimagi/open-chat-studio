import { newSpecPage } from '@stencil/core/testing';
import { OcsChat } from '../ocs-chat';

describe('open-chat-studio-widget', () => {
  it('renders', async () => {
    const page = await newSpecPage({
      components: [OcsChat],
      html: `<open-chat-studio-widget></open-chat-studio-widget>`,
    });
    expect(page.root).toEqualHtml(`
      <open-chat-studio-widget>
        <mock:shadow-root>
          <button class="btn">Chat</button>
          <dialog class="modal" id="open-chat-studio-widget">
            <form class="h-full modal-box flex flex-col" method="dialog">
              <button class="absolute btn btn-circle btn-ghost btn-sm right-2 top-2 text-gray-700">✕</button>
              <p class="font-sans text-center mt-4 text-sm text-gray-700">⚡ Powered by <a class="link" href="https://chatbots.dimagi.com">Open Chat Studio</a></p>
            </form>
            <form class="modal-backdrop" method="dialog">
              <button>close</button>
            </form>
          </dialog>
        </mock:shadow-root>
      </open-chat-studio-widget>
    `);
  });

  it('renders with visible', async () => {
    const page = await newSpecPage({
      components: [OcsChat],
      html: `<open-chat-studio-widget visible="true"></open-chat-studio-widget>`,
    });
    expect(page.root).toEqualHtml(`
      <open-chat-studio-widget visible="true">
        <mock:shadow-root>
          <button class="btn">Chat</button>
          <dialog class="modal" id="open-chat-studio-widget" open="">
            <form class="h-full modal-box flex flex-col" method="dialog">
              <button class="absolute btn btn-circle btn-ghost btn-sm right-2 top-2 text-gray-700">✕</button>
              <iframe class="flex-grow iframe-placeholder w-full"></iframe>
              <p class="font-sans text-center mt-4 text-sm text-gray-700">⚡ Powered by <a class="link" href="https://chatbots.dimagi.com">Open Chat Studio</a></p>
            </form>
            <form class="modal-backdrop" method="dialog">
              <button>close</button>
            </form>
          </dialog>
        </mock:shadow-root>
      </open-chat-studio-widget>
    `);
  });

  it('renders options', async () => {
    const page = await newSpecPage({
      components: [OcsChat],
      html: `<open-chat-studio-widget visible="true" team="test-team" bot="test-bot" scriv="http://localhost"></open-chat-studio-widget>`,
    });
    expect(page.root).toEqualHtml(`
      <open-chat-studio-widget visible="true" team="test-team" bot="test-bot" scriv="http://localhost">
        <mock:shadow-root>
          <button class="btn">Chat</button>
          <dialog class="modal" id="open-chat-studio-widget" open="">
            <form class="h-full modal-box flex flex-col" method="dialog">
              <button class="absolute btn btn-circle btn-ghost btn-sm right-2 top-2 text-gray-700">✕</button>
              <iframe class="w-full flex-grow iframe-placeholder"></iframe>
              <p class="font-sans text-center mt-4 text-sm text-gray-700">⚡ Powered by <a class="link" href="https://chatbots.dimagi.com">Open Chat Studio</a></p>
            </form>
            <form class="modal-backdrop" method="dialog">
              <button>close</button>
            </form>
          </dialog>
        </mock:shadow-root>
      </open-chat-studio-widget>
    `);
  });
});
