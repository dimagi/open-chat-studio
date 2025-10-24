import { marked } from 'marked';
import DOMPurify from 'dompurify';

marked.setOptions({
  breaks: true,
  gfm: true,
  smartypants: true,
});

/**
 * Post-processes rendered HTML to add additional attributes
 * This is called after DOMPurify to ensure external links open in new tabs
 */
export function postProcessMarkdownHTML(html: string): string {
  if (typeof window === 'undefined') {
    return html;
  }

  try {
    const tempDiv = document.createElement('div');
    tempDiv.innerHTML = html;

    // Add target="_blank" and rel="noopener noreferrer" to external links
    const links = tempDiv.querySelectorAll('a[href]');
    links.forEach((link) => {
      const href = link.getAttribute('href');
      if (href && (href.startsWith('http://') || href.startsWith('https://'))) {
        link.setAttribute('target', '_blank');
        link.setAttribute('rel', 'noopener noreferrer');
      }
    });

    return tempDiv.innerHTML;
  } catch (error) {
    console.error('Error post-processing markdown HTML:', error);
    return html;
  }
}


export function renderMarkdownSync(content: string): string {
  if (!content || typeof content !== 'string') {
    return '';
  }

  try {
    const html = marked.parse(content);
    const sanitized = DOMPurify.sanitize(html, {
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
      FORBID_TAGS: ['script', 'style', 'form', 'input', 'button'],
      FORBID_ATTR: ['onclick', 'onload', 'onerror', 'onmouseover'],
    });

    return postProcessMarkdownHTML(sanitized);
  } catch (error) {
    console.error('Error rendering markdown sync:', error);
    return DOMPurify.sanitize(content);
  }
}
