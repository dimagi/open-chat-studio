import {Component, Host, h, Prop, State, Build} from '@stencil/core';

const OCS_PROD_URL = "https://chatbots.dimagi.com";


@Component({
  tag: 'open-chat-studio-widget',
  styleUrl: 'ocs-chat.css',
  shadow: true,
})
export class OcsChat {

  @Prop() boturl!: string;
  @Prop() buttonText: string = "Chat";
  @Prop({ mutable: true }) visible: boolean = false;

  // once set this will stay true so that the iframe doesn't reload
  @State() loaded: boolean = false;

  @State() error: string = "";

  componentWillLoad() {
    this.loaded = this.visible;
    if (!Build.isDev && !this.boturl.startsWith(OCS_PROD_URL)) {
      this.error = `Invalid Bot URL: ${this.boturl}`;
    }
  }

  load() {
    this.visible = true;
    this.loaded = true;
  }

  render() {
    if (this.error) {
      return (
        <Host>
          <p>{this.error}</p>
        </Host>
      );
    }
    return (
      <Host>
        <button class="btn" onClick={() => this.load()}>{this.buttonText}</button>
        <dialog id="open-chat-studio-widget" class="modal" {...(this.visible && {open:true})} onClose={() => this.visible = false}>
          <form method="dialog" class="modal-box h-full flex flex-col">
            <button class="btn btn-sm btn-circle btn-ghost absolute right-2 top-2 text-gray-700">✕</button>
            {this.loaded && <iframe class="w-full flex-grow iframe-placeholder" src={this.boturl}></iframe>}
            <p class="font-sans text-center mt-4 text-sm text-gray-700">⚡ Powered by <a class="link" href="https://chatbots.dimagi.com">Open Chat Studio</a></p>
          </form>
          <form method="dialog" class="modal-backdrop">
            <button>close</button>
          </form>
        </dialog>
      </Host>
    );
  }
}
