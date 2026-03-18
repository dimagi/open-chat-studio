/**
 * XSS security tests for the markdown sanitization config.
 *
 * These tests run outside Stencil's test environment (which uses a mock DOM
 * that doesn't support DOMPurify) using Node's built-in test runner with
 * happy-dom for a real DOM implementation.
 *
 * Run: npm run test:security
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { Window } from 'happy-dom';
import createDOMPurify from 'dompurify';
import { marked } from 'marked';

// Mirror the config from markdown.ts — keep in sync!
const SANITIZE_CONFIG = {
  ALLOWED_TAGS: [
    'p', 'br', 'strong', 'b', 'em', 'i', 'u', 'code', 'pre',
    'ul', 'ol', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'blockquote', 'a', 'img', 'hr', 'table', 'thead', 'tbody',
    'tr', 'td', 'th', 'del', 'ins', 'sub', 'sup'
  ],
  ALLOWED_ATTR: [
    'href', 'target', 'rel', 'class', 'src', 'alt', 'title',
    'width', 'height', 'align', 'colspan', 'rowspan'
  ],
  ALLOWED_URI_REGEXP: /^(?:(?:https?):|[^a-z]|[a-z+.-]+(?:[^a-z+.\-:]|$))/i,
  ADD_ATTR: ['target'],
  FORBID_TAGS: ['script', 'style', 'form', 'input', 'button', 'iframe', 'object', 'embed', 'svg', 'math'],
  FORBID_ATTR: ['onclick', 'onload', 'onerror', 'onmouseover'],
};

const window = new Window();
const DOMPurify = createDOMPurify(window);

function sanitizeMarkdown(content) {
  const html = marked.parse(content);
  return DOMPurify.sanitize(html, SANITIZE_CONFIG);
}

function sanitizeHTML(content) {
  return DOMPurify.sanitize(content, SANITIZE_CONFIG);
}

function assertNotContains(result, substring, msg) {
  assert.ok(!result.includes(substring), msg || `Expected "${result}" not to contain "${substring}"`);
}

function assertContains(result, substring, msg) {
  assert.ok(result.includes(substring), msg || `Expected "${result}" to contain "${substring}"`);
}

describe('markdown sanitization - valid rendering', () => {
  it('renders plain text as a paragraph', () => {
    assertContains(sanitizeMarkdown('Hello world'), '<p>Hello world</p>');
  });

  it('renders bold text', () => {
    assertContains(sanitizeMarkdown('**bold**'), '<strong>bold</strong>');
  });

  it('renders links', () => {
    const result = sanitizeMarkdown('[link](https://example.com)');
    assertContains(result, 'href="https://example.com"');
    assertContains(result, '<a');
  });

  it('renders images', () => {
    const result = sanitizeMarkdown('![alt](https://example.com/img.png)');
    assertContains(result, '<img');
    assertContains(result, 'src="https://example.com/img.png"');
  });

  it('renders code blocks', () => {
    assertContains(sanitizeMarkdown('```\ncode\n```'), '<code>');
  });

  it('renders tables', () => {
    const result = sanitizeMarkdown('| A | B |\n|---|---|\n| 1 | 2 |');
    assertContains(result, '<table>');
    assertContains(result, '<td>');
  });
});

describe('XSS prevention - script injection', () => {
  it('strips script tags', () => {
    const result = sanitizeHTML('<script>alert("XSS")</script>');
    assertNotContains(result, '<script');
    assertNotContains(result, 'alert');
  });

  it('strips nested script tags (content is HTML-escaped, not executable)', () => {
    const result = sanitizeHTML('<scr<script>ipt>alert(1)</scr</script>ipt>');
    assertNotContains(result, '<script');
    // The text "alert(" may appear HTML-escaped — that's safe.
    // Verify no unescaped script tag remains.
    assert.ok(!/<script/i.test(result), `No script tags should remain: ${result}`);
  });
});

describe('XSS prevention - event handlers', () => {
  it('strips onerror on images', () => {
    const result = sanitizeHTML('<img src=x onerror=alert("XSS")>');
    assertNotContains(result, 'onerror');
    assertNotContains(result, 'alert');
  });

  it('strips onclick on links', () => {
    const result = sanitizeHTML('<a href="#" onclick="alert(1)">click</a>');
    assertNotContains(result, 'onclick');
  });

  it('strips onmouseover on allowed tags', () => {
    const result = sanitizeHTML('<p onmouseover="alert(1)">hover me</p>');
    assertNotContains(result, 'onmouseover');
  });

  it('strips onclick on allowed tags', () => {
    const result = sanitizeHTML('<p onclick="alert(1)">text</p>');
    assertNotContains(result, 'onclick');
  });

  it('strips onload on svg', () => {
    const result = sanitizeHTML('<svg onload=alert("XSS")>');
    assertNotContains(result, '<svg');
    assertNotContains(result, 'onload');
  });
});

describe('XSS prevention - dangerous URIs', () => {
  it('blocks javascript: URIs in raw HTML links', () => {
    const result = sanitizeHTML('<a href="javascript:alert(1)">click</a>');
    assertNotContains(result, 'javascript:');
  });

  it('blocks javascript: URIs in markdown links', () => {
    const result = sanitizeMarkdown('[click](javascript:alert(1))');
    assertNotContains(result, 'javascript:');
  });

  it('blocks encoded javascript: URIs', () => {
    const result = sanitizeHTML('<a href="&#106;avascript:alert(1)">click</a>');
    assertNotContains(result, 'javascript');
  });
});

describe('XSS prevention - forbidden tags', () => {
  it('strips style tags', () => {
    const result = sanitizeHTML('<style>body{background:red}</style>');
    assertNotContains(result, '<style');
  });

  it('strips form elements', () => {
    const result = sanitizeHTML('<form action="https://evil.com"><input type="text"></form>');
    assertNotContains(result, '<form');
  });

  it('strips input elements', () => {
    const result = sanitizeHTML('<input type="text" value="XSS">');
    assertNotContains(result, '<input');
  });

  it('strips button elements', () => {
    const result = sanitizeHTML('<button onclick="alert(1)">Click</button>');
    assertNotContains(result, '<button');
  });

  it('strips iframe tags', () => {
    const result = sanitizeHTML('<iframe src="https://evil.com"></iframe>');
    assertNotContains(result, '<iframe');
  });

  it('strips object tags', () => {
    const result = sanitizeHTML('<object data="evil.swf"></object>');
    assertNotContains(result, '<object');
  });

  it('strips embed tags', () => {
    const result = sanitizeHTML('<embed src="evil.swf">');
    assertNotContains(result, '<embed');
  });

  it('strips svg tags', () => {
    const result = sanitizeHTML('<svg><circle cx="50" cy="50" r="40"/></svg>');
    assertNotContains(result, '<svg');
  });

  it('strips math tags', () => {
    const result = sanitizeHTML('<math><mrow><mi>x</mi></mrow></math>');
    assertNotContains(result, '<math');
  });
});

describe('XSS prevention - mutation XSS', () => {
  it('handles mXSS via math/style nesting', () => {
    const result = sanitizeHTML(
      '<math><mtext><table><mglyph><style><!--</style><img src=x onerror=alert(1)>'
    );
    assertNotContains(result, 'onerror');
    assertNotContains(result, 'alert');
    assertNotContains(result, '<style');
    assertNotContains(result, '<math');
  });
});

describe('safe content is preserved', () => {
  it('preserves bold and italic', () => {
    const result = sanitizeHTML('<strong>bold</strong> and <em>italic</em>');
    assertContains(result, '<strong>bold</strong>');
    assertContains(result, '<em>italic</em>');
  });

  it('preserves safe links', () => {
    const result = sanitizeHTML('<a href="https://example.com">safe</a>');
    assertContains(result, 'href="https://example.com"');
  });

  it('preserves safe images', () => {
    const result = sanitizeHTML('<img src="https://example.com/img.png" alt="pic">');
    assertContains(result, 'src="https://example.com/img.png"');
    assertContains(result, 'alt="pic"');
  });

  it('preserves headings', () => {
    const result = sanitizeMarkdown('# Title\n## Subtitle');
    assertContains(result, '<h1');
    assertContains(result, '<h2');
  });

  it('preserves lists', () => {
    const result = sanitizeMarkdown('- item 1\n- item 2');
    assertContains(result, '<ul>');
    assertContains(result, '<li>');
  });
});
