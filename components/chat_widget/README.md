# Open Chat Studio Chat Component

A Web Component built with [Stencil](https://stenciljs.com/) that allows you to add a chat dialog to a web page
that is connected to a public Open Chat Studio (OCS) bot.

## Getting Started

To try this component out, run:

```bash
npm install
npm start
```

Now load the localhost URL shown in the console in your browser.

Note that this requires you to have OCS running locally on port 8000. 
You will also need to set the `boturl` property on the `open-chat-studio-widget` element to the URL of a bot you have
running locally.

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

## Using this component

There are three strategies we recommend for using web components built with Stencil.

The first step for all three of these strategies is to [publish to NPM](https://docs.npmjs.com/getting-started/publishing-npm-packages).

Once you've set up your local npm account, can do this by running

```
npm publish
```

### Script tag

- Put a script tag similar to this `<script type='module' src='https://unpkg.com/open-chat-studio-widget@0.0.1/dist/open-chat-studio-widget.esm.js'></script>` in the head of your index.html
- Then you can use the element anywhere in your template, JSX, html etc

### Node Modules
- Run `npm install open-chat-studio-widget --save`
- Put a script tag similar to this `<script type='module' src='node_modules/open-chat-studio-widget/dist/open-chat-studio-widget.esm.js'></script>` in the head of your index.html
- Then you can use the element anywhere in your template, JSX, html etc