# Open Chat Studio Chat Widget

A chatbot component for Open Chat Studio.

For more information, see the [Open Chat Studio documentation](https://docs.openchatstudio.com/how-to/embed/)

<!-- Auto Generated Below -->


## Properties

| Property                 | Attribute           | Description                                                                                                                   | Type                            | Default                         |
| ------------------------ | ------------------- | ----------------------------------------------------------------------------------------------------------------------------- | ------------------------------- | ------------------------------- |
| `apiBaseUrl`             | `api-base-url`      | The base URL for the API (defaults to current origin).                                                                        | `string`                        | `"https://chatbots.dimagi.com"` |
| `buttonShape`            | `button-shape`      | The shape of the chat button. 'default' maintains current behavior, 'round' makes it circular, 'square' makes it rectangular. | `"round" \| "square"`           | `undefined`                     |
| `buttonText`             | `button-text`       | The text to display on the button (deprecated, use iconUrl for icon button or set to non-empty string to use text button).    | `string`                        | `undefined`                     |
| `chatbotId` _(required)_ | `chatbot-id`        | The ID of the chatbot to connect to.                                                                                          | `string`                        | `undefined`                     |
| `expanded`               | `expanded`          | Whether the chat widget is initially expanded.                                                                                | `boolean`                       | `false`                         |
| `iconUrl`                | `icon-url`          | URL of the icon to display on the button. If not provided, uses the default OCS logo.                                         | `string`                        | `undefined`                     |
| `position`               | `position`          | The initial position of the chat widget on the screen.                                                                        | `"center" \| "left" \| "right"` | `'right'`                       |
| `starterQuestions`       | `starter-questions` | Array of starter questions that users can click to send (JSON array of strings)                                               | `string`                        | `undefined`                     |
| `visible`                | `visible`           | Whether the chat widget is visible on load.                                                                                   | `boolean`                       | `false`                         |
| `welcomeMessages`        | `welcome-messages`  | Welcome messages to display above starter questions (JSON array of strings)                                                   | `string`                        | `undefined`                     |


----------------------------------------------

*Built with [StencilJS](https://stenciljs.com/)*
