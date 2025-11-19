const rowHandlers = new WeakMap();

function initializeRowClickHandlers() {
    document.querySelectorAll('tr[data-redirect-url]:not([data-redirect-url=""])').forEach(function (element) {
      if (rowHandlers.has(element)) {
        const oldHandler = rowHandlers.get(element);
        element.removeEventListener('click', oldHandler);
      }

      const handler = function (event) {
        if (event.target.tagName === 'TR' || event.target.tagName === 'TD') {
          let editUrl = this.getAttribute('data-redirect-url');
          let target = this.getAttribute('data-redirect-target');
          try {
            let url = new URL(editUrl, window.location.origin);
            if (target) {
              window.open(url, target);
              return;
            }
            if (event.ctrlKey){
              window.open(url,'_blank')
            } else {
              window.location.href = url.href;
            }
          } catch {
            console.error('Invalid URL:', editUrl);
          }
        }
      };
      rowHandlers.set(element, handler);
      element.addEventListener('click', handler);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeRowClickHandlers);
  } else {
    initializeRowClickHandlers();
  }

  document.addEventListener('htmx:afterSettle', function() {
    initializeRowClickHandlers();
  });
