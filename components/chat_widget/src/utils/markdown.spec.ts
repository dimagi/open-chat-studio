import { postProcessMarkdownHTML } from './markdown';

// The DOMPurify half lives in markdown-security.test.mjs, which needs a real DOM.
// Link post-processing is plain DOM work, so it runs here against the shipped function.
describe('postProcessMarkdownHTML external links', () => {
  function opensNewTabWithoutRel(html: string): boolean {
    return html.includes('target="_blank"') && !html.includes('rel="noopener noreferrer"');
  }

  it.each([
    ['an ordinary external link', '<a href="https://example.com">x</a>'],
    ['a protocol-relative link asking for a new tab', '<a href="//evil.example" target="_blank">x</a>'],
    ['an upper case scheme', '<a href="HTTPS://evil.example" target="_blank">x</a>'],
    ['an href padded with whitespace', '<a href=" https://evil.example" target="_blank">x</a>'],
  ])('pairs target with rel for %s', (_label, input) => {
    expect(opensNewTabWithoutRel(postProcessMarkdownHTML(input))).toBe(false);
  });

  it('leaves a relative link alone', () => {
    expect(postProcessMarkdownHTML('<a href="/local/page">x</a>')).not.toContain('target="_blank"');
  });
});
