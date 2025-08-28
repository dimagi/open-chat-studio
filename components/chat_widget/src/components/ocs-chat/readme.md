# Open Chat Studio Chat Widget

A chatbot component for Open Chat Studio.

For more information, see the [Open Chat Studio documentation](https://docs.openchatstudio.com/chat_widget/)

<!-- Auto Generated Below -->


## Properties

| Property                     | Attribute                       | Description                                                                                                                       | Type                            | Default                                                                 |
| ---------------------------- | ------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- | ------------------------------- | ----------------------------------------------------------------------- |
| `allowAttachments`           | `allow-attachments`             | Allow the user to attach files to their messages.                                                                                 | `boolean`                       | `false`                                                                 |
| `allowFullScreen`            | `allow-full-screen`             | Allow the user to make the chat window full screen.                                                                               | `boolean`                       | `true`                                                                  |
| `apiBaseUrl`                 | `api-base-url`                  | The base URL for the API (defaults to current origin).                                                                            | `string`                        | `"https://chatbots.dimagi.com"`                                         |
| `buttonShape`                | `button-shape`                  | The shape of the chat button. 'round' makes it circular, 'square' keeps it rectangular.                                           | `"round" \| "square"`           | `'square'`                                                              |
| `buttonText`                 | `button-text`                   | The text to display on the button.                                                                                                | `string`                        | `undefined`                                                             |
| `chatbotId` _(required)_     | `chatbot-id`                    | The ID of the chatbot to connect to.                                                                                              | `string`                        | `undefined`                                                             |
| `headerText`                 | `header-text`                   | The text to place in the header.                                                                                                  | `""`                            | `undefined`                                                             |
| `iconUrl`                    | `icon-url`                      | URL of the icon to display on the button. If not provided, uses the default OCS logo.                                             | `string`                        | `undefined`                                                             |
| `newChatConfirmationMessage` | `new-chat-confirmation-message` | The message to display in the new chat confirmation dialog.                                                                       | `string`                        | `"Starting a new chat will clear your current conversation. Continue?"` |
| `persistentSession`          | `persistent-session`            | Whether to persist session data to local storage to allow resuming previous conversations after page reload.                      | `boolean`                       | `true`                                                                  |
| `persistentSessionExpire`    | `persistent-session-expire`     | Minutes since the most recent message after which the session data in local storage will expire. Set this to `0` to never expire. | `number`                        | `60 * 24`                                                               |
| `position`                   | `position`                      | The initial position of the chat widget on the screen.                                                                            | `"center" \| "left" \| "right"` | `'right'`                                                               |
| `starterQuestions`           | `starter-questions`             | Array of starter questions that users can click to send (JSON array of strings)                                                   | `string`                        | `undefined`                                                             |
| `typingIndicatorText`        | `typing-indicator-text`         | The text to display while the assistant is typing/preparing a response.                                                           | `string`                        | `"Preparing response"`                                                  |
| `userId`                     | `user-id`                       | Used to associate chat sessions with a specific user across multiple visits/sessions                                              | `string`                        | `undefined`                                                             |
| `userName`                   | `user-name`                     | Display name for the user.                                                                                                        | `string`                        | `undefined`                                                             |
| `visible`                    | `visible`                       | Whether the chat widget is visible on load.                                                                                       | `boolean`                       | `false`                                                                 |
| `welcomeMessages`            | `welcome-messages`              | Welcome messages to display above starter questions (JSON array of strings)                                                       | `string`                        | `undefined`                                                             |


----------------------------------------------

*Built with [StencilJS](https://stenciljs.com/)*
