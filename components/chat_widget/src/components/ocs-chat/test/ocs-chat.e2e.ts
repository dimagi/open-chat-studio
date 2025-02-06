import { newE2EPage } from '@stencil/core/testing';

describe('open-chat-studio-widget', () => {
  it('renders', async () => {
    const page = await newE2EPage();
    await page.setContent('<open-chat-studio-widget></open-chat-studio-widget>');

    const element = await page.find('open-chat-studio-widget');
    expect(element).toHaveClass('hydrated');
  });
});
