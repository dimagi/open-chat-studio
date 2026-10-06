import * as alertify from 'alertifyjs'
window.alertify = alertify;

// Canonical pattern for confirming a destructive htmx action with the styled
// alertify modal instead of the native `hx-confirm` browser dialog. Pair with
// `hx-trigger="confirmed"` and a unique element id on the triggering element.
window.confirmThenTrigger = function (elementId, message, optionsTemplateId) {
  const options = optionsTemplateId ? document.getElementById(optionsTemplateId) : null;
  alertify.confirm().setting({
    title: 'Confirm',
    message: options ? message + options.innerHTML : message,
    transition: 'fade',
    onok: function () {
      const element = document.getElementById(elementId);
      if (!element) {
        return;
      }
      if (options) {
        const values = {};
        document
          .querySelectorAll('.alertify:not(.ajs-hidden) input[type="checkbox"][name]')
          .forEach((checkbox) => {
            if (checkbox.checked) {
              values[checkbox.name] = 'on';
            }
          });
        element.setAttribute('hx-vals', JSON.stringify(values));
      }
      htmx.trigger(element, 'confirmed');
    },
  }).show();
};
