import * as alertify from 'alertifyjs'
window.alertify = alertify;

// Styled replacement for the browser's `confirm()`. Resolves true when the user confirms.
// See docs/agents/ui_components.md.
window.confirmDialog = function (message) {
  // A dialog opened with showModal() renders above the rest of the page and blocks clicks
  // outside it, so the confirm goes inside it. Esc would otherwise close that dialog too.
  const openModal = document.querySelector('dialog:modal');
  const keepModalOpen = (event) => event.preventDefault();
  openModal?.addEventListener('cancel', keepModalOpen);
  return new Promise((resolve) => {
    const finish = (confirmed) => {
      openModal?.removeEventListener('cancel', keepModalOpen);
      resolve(confirmed);
    };
    const dialog = alertify.confirm().setting({
      title: 'Confirm',
      message: message,
      transition: 'fade',
      onok: () => finish(true),
      oncancel: () => finish(false),
      onclose: () => finish(false),
    });
    dialog.show();
    (openModal ?? document.body).appendChild(dialog.elements.root);
  });
};

// Confirm before an htmx request. Pair with `hx-trigger="confirmed"` and a unique element id
// on the triggering element. `optionsTemplateId` names an element whose checkboxes are shown in
// the dialog and sent as `hx-vals`.
window.confirmThenTrigger = function (elementId, message, optionsTemplateId) {
  const options = optionsTemplateId ? document.getElementById(optionsTemplateId) : null;
  window.confirmDialog(options ? message + options.innerHTML : message).then((confirmed) => {
    const element = document.getElementById(elementId);
    if (!confirmed || !element) {
      return;
    }
    if (options) {
      const values = {};
      alertify
        .confirm()
        .elements.content.querySelectorAll('input[type="checkbox"][name]')
        .forEach((checkbox) => {
          if (checkbox.checked) {
            values[checkbox.name] = 'on';
          }
        });
      element.setAttribute('hx-vals', JSON.stringify(values));
    }
    htmx.trigger(element, 'confirmed');
  });
};

// Confirm before a plain form POST: `onsubmit="confirmThenSubmit(event, '...')"`.
// Resubmits with the original submit button so its name and value are still posted.
window.confirmThenSubmit = function (event, message) {
  const form = event.target;
  if (form.dataset.confirmed) {
    delete form.dataset.confirmed;
    return;
  }
  event.preventDefault();
  const submitter = event.submitter;
  window.confirmDialog(message).then((confirmed) => {
    if (confirmed) {
      form.dataset.confirmed = 'true';
      form.requestSubmit(submitter);
    }
  });
};
