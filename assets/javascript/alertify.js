import * as alertify from 'alertifyjs'
window.alertify = alertify;

// Canonical pattern for confirming a destructive htmx action with the styled
// alertify modal instead of the native `hx-confirm` browser dialog. Pair with
// `hx-trigger="confirmed"` and a unique element id on the triggering element.
window.confirmThenTrigger = function (elementId, message) {
  alertify.confirm().setting({
    title: 'Confirm',
    message: message,
    transition: 'fade',
    onok: function () {
      htmx.trigger('#' + elementId, 'confirmed');
    },
  }).show();
};
