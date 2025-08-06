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

| Name                              | Description                         |
| --------------------------------- | ----------------------------------- |
| `--button-background-color`       | Button background color             |
| `--button-background-color-hover` | Button background color on hover    |
| `--button-border-color`           | Button border color                 |
| `--button-border-color-hover`     | Button border color on hover        |
| `--button-text-color`             | Button text color                   |
| `--button-text-color-hover`       | Button text color on hover          |
| `--color-background`              | Main background color               |
| `--color-border`                  | Default border color                |
| `--color-error`                   | Error/red color                     |
| `--color-focus`                   | Focus ring color                    |
| `--color-gray-100`                | Light gray                          |
| `--color-gray-200`                | Medium light gray                   |
| `--color-gray-300`                | Medium gray                         |
| `--color-gray-400`                | Dark medium gray                    |
| `--color-gray-50`                 | Very light gray                     |
| `--color-gray-500`                | Dark gray                           |
| `--color-gray-600`                | Very dark gray                      |
| `--color-gray-700`                | Extra dark gray                     |
| `--color-gray-800`                | Almost black gray                   |
| `--color-gray-900`                | Almost black                        |
| `--color-message-assistant-bg`    | Assistant message background color  |
| `--color-message-assistant-text`  | Assistant message text color        |
| `--color-message-system-bg`       | System message background color     |
| `--color-message-system-text`     | System message text color           |
| `--color-message-user-bg`         | User message background color       |
| `--color-message-user-text`       | User message text color             |
| `--color-primary`                 | Primary blue color                  |
| `--color-primary-hover`           | Primary blue hover color            |
| `--color-primary-light`           | Light primary color for backgrounds |
| `--color-success`                 | Success/green color                 |
| `--color-text-primary`            | Primary text color                  |
| `--color-text-secondary`          | Secondary text color                |
| `--color-white`                   | White color                         |
| `--padding-button`                | Button padding (0.75rem)            |
| `--padding-button-sm`             | Small button padding (0.375rem)     |
| `--padding-container`             | Container padding                   |
| `--padding-lg`                    | Large padding (1rem)                |
| `--padding-md`                    | Medium padding (0.75rem)            |
| `--padding-message`               | Message bubble padding              |
| `--padding-sm`                    | Small padding (0.5rem)              |
| `--padding-xl`                    | Extra large padding (1.5rem)        |
| `--padding-xs`                    | Extra small padding (0.125rem)      |


----------------------------------------------

*Built with [StencilJS](https://stenciljs.com/)*
