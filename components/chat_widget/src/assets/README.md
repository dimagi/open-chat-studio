# Chat Widget Translations

This directory contains locale bundles for the `open-chat-studio-widget`. Each file under `translations/` is a flat JSON map keyed by the widget's translation identifiers. The English bundle (`en.json`) acts as the reference: new strings belong there first, then should be copied to the other locale files for translation.

## Adding Or Updating Locales

- Duplicate `en.json` when introducing a new language and update the values with the translated strings.
- Keep the key ordering consistent with `en.json` so diffs stay legible.
- Arrays such as `content.welcomeMessages` and `content.starterQuestions` should remain arrays; provide localized entries or leave them empty to fall back to the widget props.
- Leave optional branding overrides (`branding.buttonText`, `branding.headerText`) blank when you want the runtime props to provide the value.

## Translation Key Reference

### launcher
- `launcher.open` — Default action label for the launcher button; also used for the aria-label and tooltip when no branding text is supplied.

### window
- `window.close` — Closes the chat window.
- `window.newChat` — Starts a new chat session from the launcher menu.
- `window.fullscreen` — Enters fullscreen mode.
- `window.exitFullscreen` — Leaves fullscreen mode.

### attach
- `attach.add` — Button text for adding file attachments.
- `attach.remove` — Removes a pending attachment.
- `attach.success` — Snackbar/toast message when a file upload is queued successfully.

### status
- `status.starting` — Displayed while the chat session initializes.
- `status.typing` — Shown while the assistant prepares a response.
- `status.uploading` — Indicates an attachment upload is in progress.

### modal
- `modal.newChatTitle` — Title for the "start new chat" confirmation dialog.
- `modal.newChatBody` — Body text explaining the effect of starting a new chat.
- `modal.cancel` — Cancel button copy in dialogs.
- `modal.confirm` — Confirmation button copy in dialogs.

### composer
- `composer.placeholder` — Placeholder text inside the message composer input.
- `composer.send` — Send button text.

### error
- `error.fileTooLarge` — Error shown when a single file exceeds the size limit.
- `error.totalTooLarge` — Error when combined attachment size exceeds the limit.
- `error.unsupportedType` — Error for unsupported file formats.
- `error.connection` — Generic network or API error message.
- `error.sessionExpired` — Prompt shown when the chat session expires.

### branding
- `branding.poweredBy` — Precedes the logo/link in the footer (“Powered by …”).
- `branding.buttonText` — Overrides the launcher button copy; blank values fall back to the `buttonText` prop.
- `branding.headerText` — Optional override for the header title; blank values fall back to the `headerText` prop.

### content
- `content.welcomeMessages` — Array of initial messages displayed in the conversation window; empty arrays fall back to the widget prop.
- `content.starterQuestions` — Array of suggested starter questions; empty arrays fall back to the widget prop.

Keep this reference in sync with `en.json` whenever new keys are introduced or semantics change, so translators have up-to-date context.
