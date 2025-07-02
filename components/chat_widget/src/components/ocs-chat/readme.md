# Open Chat Studio Chat Widget

A chatbot component for Open Chat Studio.

For more information, see the [Open Chat Studio documentation](https://docs.openchatstudio.com/how-to/embed/)

<!-- Auto Generated Below -->


## Properties

| Property                 | Attribute      | Description                                            | Type                            | Default                         |
| ------------------------ | -------------- | ------------------------------------------------------ | ------------------------------- | ------------------------------- |
| `apiBaseUrl`             | `api-base-url` | The base URL for the API (defaults to current origin). | `string`                        | `"https://chatbots.dimagi.com"` |
| `buttonText`             | `button-text`  | The text to display on the button.                     | `string`                        | `"Chat"`                        |
| `chatbotId` _(required)_ | `chatbot-id`   | The ID of the chatbot to connect to.                   | `string`                        | `undefined`                     |
| `expanded`               | `expanded`     | Whether the chat widget is initially expanded.         | `boolean`                       | `false`                         |
| `position`               | `position`     | The initial position of the chat widget on the screen. | `"center" \| "left" \| "right"` | `'right'`                       |
| `visible`                | `visible`      | Whether the chat widget is visible on load.            | `boolean`                       | `false`                         |


## CSS Custom Properties

| Name                              | Description                      |
| --------------------------------- | -------------------------------- |
| `--button-background-color`       | Button background color          |
| `--button-background-color-hover` | Button background color on hover |
| `--button-border-color`           | Button border color              |
| `--button-border-color-hover`     | Button border color on hover     |
| `--button-text-color`             | Button text color                |
| `--button-text-color-hover`       | Button text color on hover       |


----------------------------------------------

*Built with [StencilJS](https://stenciljs.com/)*
