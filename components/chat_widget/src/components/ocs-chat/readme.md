# Open Chat Studio Chat Widget

A chatbot component for Open Chat Studio.

For more information, see the [Open Chat Studio documentation](https://docs.openchatstudio.com/chat_widget/)

<!-- Auto Generated Below -->


## Properties

| Property                     | Attribute                       | Description                                                                                                                       | Type                            | Default                            |
| ---------------------------- | ------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- | ------------------------------- | ---------------------------------- |
| `allowAttachments`           | `allow-attachments`             | Allow the user to attach files to their messages.                                                                                 | `boolean`                       | `false`                            |
| `allowFullScreen`            | `allow-full-screen`             | Allow the user to make the chat window full screen.                                                                               | `boolean`                       | `true`                             |
| `apiBaseUrl`                 | `api-base-url`                  | The base URL for the API.                                                                                                         | `string`                        | `"https://www.openchatstudio.com"` |
| `buttonShape`                | `button-shape`                  | The shape of the chat button. 'round' makes it circular, 'square' keeps it rectangular.                                           | `"round" \| "square"`           | `'square'`                         |
| `buttonText`                 | `button-text`                   | The text to display on the button.                                                                                                | `string`                        | `undefined`                        |
| `chatbotId` _(required)_     | `chatbot-id`                    | The ID of the chatbot to connect to.                                                                                              | `string`                        | `undefined`                        |
| `embedKey`                   | `embed-key`                     | Authentication key for embedded channels                                                                                          | `string`                        | `undefined`                        |
| `headerText`                 | `header-text`                   | The text to place in the header.                                                                                                  | `""`                            | `undefined`                        |
| `iconUrl`                    | `icon-url`                      | URL of the icon to display on the button. If not provided, uses the default OCS logo.                                             | `string`                        | `undefined`                        |
| `language`                   | `language`                      | The language code for the widget UI (e.g., 'en', 'es', 'fr'). Defaults to en                                                      | `string`                        | `undefined`                        |
| `newChatConfirmationMessage` | `new-chat-confirmation-message` | The message to display in the new chat confirmation dialog.                                                                       | `string`                        | `undefined`                        |
| `persistentSession`          | `persistent-session`            | Whether to persist session data to local storage to allow resuming previous conversations after page reload.                      | `boolean`                       | `true`                             |
| `persistentSessionExpire`    | `persistent-session-expire`     | Minutes since the most recent message after which the session data in local storage will expire. Set this to `0` to never expire. | `number`                        | `60 * 24`                          |
| `position`                   | `position`                      | The initial position of the chat widget on the screen.                                                                            | `"center" \| "left" \| "right"` | `'right'`                          |
| `starterQuestions`           | `starter-questions`             | Array of starter questions that users can click to send (JSON array of strings)                                                   | `string`                        | `undefined`                        |
| `translationsUrl`            | `translations-url`              |                                                                                                                                   | `string`                        | `undefined`                        |
| `typingIndicatorText`        | `typing-indicator-text`         | The text to display while the assistant is typing/preparing a response.                                                           | `string`                        | `undefined`                        |
| `userId`                     | `user-id`                       | Used to associate chat sessions with a specific user across multiple visits/sessions                                              | `string`                        | `undefined`                        |
| `userName`                   | `user-name`                     | Display name for the user.                                                                                                        | `string`                        | `undefined`                        |
| `visible`                    | `visible`                       | Whether the chat widget is visible on load.                                                                                       | `boolean`                       | `false`                            |
| `welcomeMessages`            | `welcome-messages`              | Welcome messages to display above starter questions (JSON array of strings)                                                       | `string`                        | `undefined`                        |


## CSS Custom Properties

| Name                                           | Description                                                                      |
| ---------------------------------------------- | -------------------------------------------------------------------------------- |
| `--button-background-color`                    | Button background color (#ffffff)                                                |
| `--button-background-color-hover`              | Button background color on hover (#f3f4f6)                                       |
| `--button-border-color`                        | Button border color (#6b7280)                                                    |
| `--button-border-color-hover`                  | Button border color on hover (#374151)                                           |
| `--button-font-size`                           | Button text font size (0.875em)                                                  |
| `--button-icon-size`                           | Button icon size (1.5em)                                                         |
| `--button-text-color`                          | Button text color (#111827)                                                      |
| `--button-text-color-hover`                    | Button text color on hover (#1d4ed8)                                             |
| `--chat-window-bg-color`                       | Chat window background color (#ffffff)                                           |
| `--chat-window-border-color`                   | Chat window border color (#d1d5db)                                               |
| `--chat-window-font-size`                      | Default font size for text in the chat window (0.875em)                          |
| `--chat-window-font-size-sm`                   | Font size for small text in the chat window (0.75em)                             |
| `--chat-window-fullscreen-width`               | Chat window fullscreen width in pixels or percent (80%)                          |
| `--chat-window-height`                         | Chat window height in pixels or percent (60%)                                    |
| `--chat-window-shadow-color`                   | Chat window shadow color (rgba(0, 0, 0, 0.1))                                    |
| `--chat-window-width`                          | Chat window width in pixels or percent (25%)                                     |
| `--chat-z-index`                               | Z-index for chat widget (50)                                                     |
| `--code-bg-assistant-color`                    | Code background in assistant messages (--message-assistant-bg-color + 50% white) |
| `--code-bg-user-color`                         | Code background in user messages (--message-user-bg-color + 20% white)           |
| `--code-border-assistant-color`                | Code border in assistant messages (--message-assistant-bg-color + 10% black)     |
| `--code-border-user-color`                     | Code border in user messages (--message-user-bg-color + 20% black)               |
| `--code-text-assistant-color`                  | Code text color in assistant messages (--message-assistant-text-color)           |
| `--code-text-user-color`                       | Code text color in user messages (--message-user-text-color)                     |
| `--confirmation-button-cancel-bg-color`        | Cancel button background color (uses --button-background-color-hover)            |
| `--confirmation-button-cancel-bg-hover-color`  | Cancel button background on hover (uses #e5e7eb)                                 |
| `--confirmation-button-cancel-text-color`      | Cancel button text color (uses --header-button-text-color)                       |
| `--confirmation-button-confirm-bg-color`       | Confirm button background color (uses --error-text-color)                        |
| `--confirmation-button-confirm-bg-hover-color` | Confirm button background on hover (uses --error-text-color)                     |
| `--confirmation-button-confirm-text-color`     | Confirm button text color (uses --send-button-text-color)                        |
| `--confirmation-dialog-bg-color`               | Confirmation dialog background color (uses --chat-window-bg-color)               |
| `--confirmation-dialog-border-color`           | Confirmation dialog border color (uses --chat-window-border-color)               |
| `--confirmation-dialog-shadow-color`           | Confirmation dialog shadow color (uses --chat-window-shadow-color)               |
| `--confirmation-message-color`                 | Confirmation dialog message text color (uses --loading-text-color)               |
| `--confirmation-message-font-size`             | Confirmation dialog message font size (uses 1em)                                 |
| `--confirmation-overlay-bg-color`              | Confirmation dialog overlay background color (rgba(0, 0, 0, 0.5))                |
| `--confirmation-title-color`                   | Confirmation dialog title text color (uses #111827)                              |
| `--confirmation-title-font-size`               | Confirmation dialog title font size (1.125em)                                    |
| `--error-text-color`                           | Error text color (#ef4444)                                                       |
| `--file-attachment-button-bg-color`            | Attach file button background color (transparent)                                |
| `--file-attachment-button-bg-hover-color`      | Attach file button background hover color (--header-button-bg-hover-color)       |
| `--file-attachment-button-text-color`          | Attach file button text color (--header-button-text-color)                       |
| `--file-attachment-button-text-disabled-color` | Attach file button disabled text color (--send-button-text-disabled-color)       |
| `--header-bg-color`                            | Header background color (transparent)                                            |
| `--header-bg-hover-color`                      | Header background color on hover (#f9fafb)                                       |
| `--header-border-color`                        | Header border color (#f3f4f6)                                                    |
| `--header-button-bg-hover-color`               | Header button background on hover (#f3f4f6)                                      |
| `--header-button-icon-size`                    | Icon size for buttons in the header (1.5em)                                      |
| `--header-button-text-color`                   | Header button text color (#6b7280)                                               |
| `--header-font-size`                           | Header font size (1em)                                                           |
| `--header-text-color`                          | Color for the text in the header (#525762)                                       |
| `--header-text-font-size`                      | Font size for the text in the header (1em)                                       |
| `--input-bg-color`                             | Input area background color (transparent)                                        |
| `--input-border-color`                         | Input field border color (#d1d5db)                                               |
| `--input-outline-focus-color`                  | Input field focus ring color (#3b82f6)                                           |
| `--input-placeholder-color`                    | Input placeholder text color (#6b7280)                                           |
| `--input-text-color`                           | Input text color (#111827)                                                       |
| `--loading-spinner-fill-color`                 | Loading spinner fill color (#3b82f6)                                             |
| `--loading-spinner-size`                       | Loading spinner size (1.25em)                                                    |
| `--loading-spinner-track-color`                | Loading spinner track color (#e5e7eb)                                            |
| `--loading-text-color`                         | Loading text color (#6b7280)                                                     |
| `--message-assistant-bg-color`                 | Assistant message background color (#eae7e8)                                     |
| `--message-assistant-link-color`               | Assistant message link color (--message-user-link-color)                         |
| `--message-assistant-text-color`               | Assistant message text color (--message-user-text-color)                         |
| `--message-attachment-icon-size`               | Message attachment icon size (1em)                                               |
| `--message-system-bg-color`                    | System message background color (#fbe4f8)                                        |
| `--message-system-link-color`                  | System message link color (--message-user-link-color)                            |
| `--message-system-text-color`                  | System message text color (--message-user-text-color)                            |
| `--message-timestamp-assistant-color`          | Assistant message timestamp color (rgba(75, 85, 99, 0.7))                        |
| `--message-timestamp-color`                    | User message timestamp color (rgba(255, 255, 255, 0.7))                          |
| `--message-user-bg-color`                      | User message background color (#e4edfb)                                          |
| `--message-user-link-color`                    | User message link color (#155dfc)                                                |
| `--message-user-text-color`                    | User message text color (#1f2937)                                                |
| `--scrollbar-thumb-color`                      | Scrollbar thumb color (#d1d5db)                                                  |
| `--scrollbar-thumb-hover-color`                | Scrollbar thumb hover color (#9ca3af)                                            |
| `--scrollbar-track-color`                      | Scrollbar track color (#f3f4f6)                                                  |
| `--selected-file-bg-color`                     | Selected file item background color (--message-system-bg-color)                  |
| `--selected-file-font-size`                    | Selected file item font size (--chat-window-font-size-sm)                        |
| `--selected-file-icon-size`                    | Selected file item icon size (1.25em)                                            |
| `--selected-file-name-color`                   | Selected file name color (--message-assistant-text-color)                        |
| `--selected-file-remove-icon-color`            | Selected file remove icon color (--error-text-color)                             |
| `--selected-file-remove-icon-hover-color`      | Selected file remove icon hover (#dc2626)                                        |
| `--selected-file-size-color`                   | Selected file size display color (--input-placeholder-color)                     |
| `--selected-files-bg-color`                    | Selected files container background color (--chat-window-bg-color)               |
| `--selected-files-border-color`                | Selected files container border color (--header-border-color)                    |
| `--send-button-bg-color`                       | Send button background color (#3b82f6)                                           |
| `--send-button-bg-disabled-color`              | Send button background when disabled (#d1d5db)                                   |
| `--send-button-bg-hover-color`                 | Send button background on hover (#2563eb)                                        |
| `--send-button-text-color`                     | Send button text color (#ffffff)                                                 |
| `--send-button-text-disabled-color`            | Send button text when disabled (#6b7280)                                         |
| `--starter-question-bg-color`                  | Starter question background color (transparent)                                  |
| `--starter-question-bg-hover-color`            | Starter question background on hover (#eff6ff)                                   |
| `--starter-question-border-color`              | Starter question border color (#3b82f6)                                          |
| `--starter-question-border-hover-color`        | Starter question border on hover (#2563eb)                                       |
| `--starter-question-text-color`                | Starter question text color (#3b82f6)                                            |
| `--success-text-color`                         | Success text color (#10b981)                                                     |
| `--typing-progress-bg-color`                   | Typing progress bar background color (#ade3ff)                                   |


----------------------------------------------

*Built with [StencilJS](https://stenciljs.com/)*
