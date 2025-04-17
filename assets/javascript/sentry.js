import * as Sentry from "@sentry/browser";

const global_config = JSON.parse(document.getElementById("global_config").textContent);
if (global_config.sentry_dsn) {
  Sentry.init({
    dsn: global_config.sentry_dsn,
    sendDefaultPii: true,
    integrations: [
      Sentry.browserTracingIntegration(),
      Sentry.replayIntegration(),
    ],
    tracesSampleRate: 0.1,
    replaysSessionSampleRate: 0.1,
    replaysOnErrorSampleRate: 1.0,
  });
}
