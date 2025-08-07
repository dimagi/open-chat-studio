# Open Chat Studio Chat Widget

A chatbot component for Open Chat Studio.

For more information, see the [Open Chat Studio documentation](https://docs.openchatstudio.com/chat_widget/)

<!-- Auto Generated Below -->


## Properties

| Property                  | Attribute                   | Description                                                                                                                       | Type                            | Default                         |
| ------------------------- | --------------------------- | --------------------------------------------------------------------------------------------------------------------------------- | ------------------------------- | ------------------------------- |
| `allowFullScreen`         | `allow-full-screen`         | Allow the user to make the chat window full screen.                                                                               | `boolean`                       | `true`                          |
| `apiBaseUrl`              | `api-base-url`              | The base URL for the API (defaults to current origin).                                                                            | `string`                        | `"https://chatbots.dimagi.com"` |
| `buttonShape`             | `button-shape`              | The shape of the chat button. 'round' makes it circular, 'square' keeps it rectangular.                                           | `"round" \| "square"`           | `'square'`                      |
| `buttonText`              | `button-text`               | The text to display on the button.                                                                                                | `string`                        | `undefined`                     |
| `chatbotId` _(required)_  | `chatbot-id`                | The ID of the chatbot to connect to.                                                                                              | `string`                        | `undefined`                     |
| `iconUrl`                 | `icon-url`                  | URL of the icon to display on the button. If not provided, uses the default OCS logo.                                             | `string`                        | `undefined`                     |
| `persistentSession`       | `persistent-session`        | Whether to persist session data to local storage to allow resuming previous conversations after page reload.                      | `boolean`                       | `true`                          |
| `persistentSessionExpire` | `persistent-session-expire` | Minutes since the most recent message after which the session data in local storage will expire. Set this to `0` to never expire. | `number`                        | `60 * 24`                       |
| `position`                | `position`                  | The initial position of the chat widget on the screen.                                                                            | `"center" \| "left" \| "right"` | `'right'`                       |
| `starterQuestions`        | `starter-questions`         | Array of starter questions that users can click to send (JSON array of strings)                                                   | `string`                        | `undefined`                     |
| `userId`                  | `user-id`                   | Used to associate chat sessions with a specific user across multiple visits/sessions                                              | `string`                        | `undefined`                     |
| `userName`                | `user-name`                 | Display name for the user.                                                                                                        | `string`                        | `undefined`                     |
| `visible`                 | `visible`                   | Whether the chat widget is visible on load.                                                                                       | `boolean`                       | `false`                         |
| `welcomeMessages`         | `welcome-messages`          | Welcome messages to display above starter questions (JSON array of strings)                                                       | `string`                        | `undefined`                     |


## CSS Custom Properties

| Name                                    | Description                                                            |
| --------------------------------------- | ---------------------------------------------------------------------- |
| `--button-background-color`             | Button background color (#ffffff)                                      |
| `--button-background-color-hover`       | Button background color on hover (#f3f4f6)                             |
| `--button-border-color`                 | Button border color (#6b7280)                                          |
| `--button-border-color-hover`           | Button border color on hover (#374151)                                 |
| `--button-font-size`                    | Button text font size (0.875rem)                                       |
| `--button-icon-height`                  | Button icon height (1.5rem) Chat Window Variables                      |
| `--button-icon-width`                   | Button icon width (1.5rem)                                             |
| `--button-padding`                      | Button padding (0.75rem)                                               |
| `--button-padding-sm`                   | Small button padding (0.375rem)                                        |
| `--button-text-color`                   | Button text color (#111827)                                            |
| `--button-text-color-hover`             | Button text color on hover (#1d4ed8)                                   |
| `--chat-window-bg-color`                | Chat window background color (#ffffff)                                 |
| `--chat-window-border-color`            | Chat window border color (#d1d5db)                                     |
| `--chat-window-font-size`               | Default font size for text in the chat window (0.875rem)               |
| `--chat-window-font-size-sm`            | Font size for small text in the chat window (0.75rem) Header Variables |
| `--chat-window-shadow-color`            | Chat window shadow color (rgba(0, 0, 0, 0.1))                          |
| `--chat-z-index`                        | Z-index for chat widget (50)                                           |
| `--code-bg-assistant-color`             | Code background in assistant messages (#ffffff)                        |
| `--code-bg-user-color`                  | Code background in user messages (rgba(59, 130, 246, 0.3))             |
| `--code-border-assistant-color`         | Code border in assistant messages (#d1d5db)                            |
| `--code-border-user-color`              | Code border in user messages (rgba(59, 130, 246, 0.6))                 |
| `--code-text-assistant-color`           | Code text color in assistant messages (#1f2937)                        |
| `--code-text-user-color`                | Code text color in user messages (#dbeafe)                             |
| `--container-padding`                   | General container padding (1rem) Button Variables                      |
| `--error-message-padding`               | Error message padding (0.5rem) Markdown Code Variables                 |
| `--error-text-color`                    | Error text color (#ef4444)                                             |
| `--header-bg-color`                     | Header background color (transparent)                                  |
| `--header-bg-hover-color`               | Header background color on hover (#f9fafb)                             |
| `--header-border-color`                 | Header border color (#f3f4f6)                                          |
| `--header-button-bg-hover-color`        | Header button background on hover (#f3f4f6)                            |
| `--header-button-text-color`            | Header button text color (#6b7280)                                     |
| `--header-padding`                      | Header padding (0.5rem) Starter Question Variables                     |
| `--input-bg-color`                      | Input area background color (transparent)                              |
| `--input-border-color`                  | Input field border color (#d1d5db)                                     |
| `--input-outline-focus-color`           | Input field focus ring color (#3b82f6)                                 |
| `--input-placeholder-color`             | Input placeholder text color (#6b7280)                                 |
| `--input-text-color`                    | Input text color (#111827)                                             |
| `--input-textarea-padding-x`            | Input textarea horizontal padding (0.75rem)                            |
| `--input-textarea-padding-y`            | Input textarea vertical padding (0.5rem) Send Button Variables         |
| `--loading-spinner-fill-color`          | Loading spinner fill color (#3b82f6)                                   |
| `--loading-spinner-size`                | Loading spinner size (1.25rem) Typing Indicator Variables              |
| `--loading-spinner-track-color`         | Loading spinner track color (#e5e7eb)                                  |
| `--loading-text-color`                  | Loading text color (#6b7280)                                           |
| `--message-assistant-bg-color`          | Assistant message background color (#e5e7eb)                           |
| `--message-assistant-text-color`        | Assistant message text color (#1f2937)                                 |
| `--message-padding-x`                   | Message horizontal padding (1rem)                                      |
| `--message-padding-y`                   | Message vertical padding (0.5rem) Input Area Variables                 |
| `--message-system-bg-color`             | System message background color (#f3f4f6)                              |
| `--message-system-text-color`           | System message text color (#4b5563)                                    |
| `--message-timestamp-assistant-color`   | Assistant message timestamp color (rgba(75, 85, 99, 0.7))              |
| `--message-timestamp-color`             | User message timestamp color (rgba(255, 255, 255, 0.7))                |
| `--message-user-bg-color`               | User message background color (#3b82f6)                                |
| `--message-user-text-color`             | User message text color (#ffffff)                                      |
| `--scrollbar-thumb-color`               | Scrollbar thumb color (#d1d5db)                                        |
| `--scrollbar-thumb-hover-color`         | Scrollbar thumb hover color (#9ca3af) Error Variables                  |
| `--scrollbar-track-color`               | Scrollbar track color (#f3f4f6)                                        |
| `--send-button-bg-color`                | Send button background color (#3b82f6)                                 |
| `--send-button-bg-disabled-color`       | Send button background when disabled (#d1d5db)                         |
| `--send-button-bg-hover-color`          | Send button background on hover (#2563eb)                              |
| `--send-button-padding-x`               | Send button horizontal padding (1rem)                                  |
| `--send-button-padding-y`               | Send button vertical padding (0.5rem) Loading Variables                |
| `--send-button-text-color`              | Send button text color (#ffffff)                                       |
| `--send-button-text-disabled-color`     | Send button text when disabled (#6b7280)                               |
| `--starter-question-bg-color`           | Starter question background color (transparent)                        |
| `--starter-question-bg-hover-color`     | Starter question background on hover (#eff6ff)                         |
| `--starter-question-border-color`       | Starter question border color (#3b82f6)                                |
| `--starter-question-border-hover-color` | Starter question border on hover (#2563eb)                             |
| `--starter-question-padding`            | Starter question padding (0.75rem) Message Bubble Variables            |
| `--starter-question-text-color`         | Starter question text color (#3b82f6)                                  |
| `--typing-progress-bg-color`            | Typing progress bar background color (#ade3ff) Scrollbar Variables     |


----------------------------------------------

*Built with [StencilJS](https://stenciljs.com/)*
