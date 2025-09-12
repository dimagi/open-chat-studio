# Open Chat Studio Chat Component

A Web Component built with [Stencil](https://stenciljs.com/) that allows you to add a native chat interface to any web page
that connects directly to the Open Chat Studio (OCS) Chat API.

## Features

- **Native Chat Interface**: No iframe required - renders as native web components
- **Real-time Messaging**: Send and receive messages with typing indicators
- **Responsive Design**: Works on desktop and mobile devices
- **Customizable**: Configurable positioning, styling, and behavior
- **Session Management**: Persistent chat sessions with automatic reconnection
- **Error Handling**: Graceful error handling and recovery

## Getting Started

To try this component out, run:

```bash
npm install
npm start
```

Now load the localhost URL shown in the console in your browser.

**Note**: You will need a valid chatbot ID from your OCS instance. The component uses the following API endpoints:
- `POST /api/chat/start/` - Start new chat session
- `POST /api/chat/{session_id}/message/` - Send messages
- `GET /api/chat/{session_id}/{task_id}/poll/` - Poll for responses
- `GET /api/chat/{session_id}/poll/` - Poll for new messages

To build the component for production, run:

```bash
npm run build
```

To run the unit tests for the components, run:

```bash
npm test
```

## Making Changes

To make changes to the component, you can edit the files in the `src/components/open-chat-studio-widget` directory. You can
also edit the `src/index.html` file to change the page that is loaded when you run `npm start`.

### Styling

The component uses [Tailwind CSS](https://tailwindcss.com/) with [DaisyUI](https://daisyui.com/) for styling.

## Publishing

This widget is published via GitHub actions. To trigger the action:

* Update the version number in `package.json`.
* Tag the repo with `w_v{version number}`.
* Push the changes and the tag to `main`.