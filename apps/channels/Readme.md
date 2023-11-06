## Facebook channel integration
TODO: Move this to the [Public Wiki](https://github.com/dimagi/open-chat-studio/wiki/Experiment-Channels) page.

In order to connect your bot to Facebook Messenger, you need to have admin privileges for an existing Facebook Page.

### 1. Create a Meta App
1. Sign into Meta and [create an App](https://developers.facebook.com/apps/). Choose "Business"  ass the app type (When asked about the app's purpose, select "Other" -> Business).

2. Once your app is created, access the app's dashboard and set up the `Messenger` product. This will redirect you to the `Messenger` product's settings page, where you can add or create a new Facebook Page under the `Access Tokens` section. After linking your page, you'll see it listed with its page ID (usually a numeric value like 158407932224811). Make sure to also generate a token for this page and securely store both the token and page ID.

### 2. Add the facebook channel on OpenChatStudio
Go to your experiment on OpenChatStudio and add a Facebook channel. A popup will appear and ask you for the page id
and page access token (this is the token that was generated). You'll notice another field called "Verify Token" that will be pre-populated. This is a generated verification token that you must use when registering webhooks to the OpenChatStudio server. Save the channel, and continue to the next section.


### 3. Set up a webhook from the Meta App to OpenChatStudio
1. In your Meta App's settings, under the Webhooks section, click on "Add Callback URL" and provide `https://chatbots.dimagi.com/channels/facebook/<your team slug>/incoming_message` as the callback URL. The Verify Token to use here is the one you saw when creating the Facebook channel in OpenChatStudio.

2. Once the webhook is set up, click on the `Add Subscriptions` button on the right hand side of the webhooks section and
subscribe to the `messages` field. Currently only this field is supported.

### 4. Making the app live
To be determined