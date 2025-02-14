import { Component, Host, h, Prop, State, Build } from '@stencil/core';
import {
  ArrowLeftEndOnRectangleIcon,
  ArrowRightEndOnRectangleIcon,
  ViewfinderCircleIcon,
  XMarkIcon,
  ChevronDownIcon,
  ChevronUpIcon,
} from './heroicons';

const allowedHosts = ["chatbots.dimagi.com"];

@Component({
  tag: 'open-chat-studio-widget',
  styleUrl: 'ocs-chat.css',
  shadow: true,
})
export class OcsChat {

  /**
   * The URL of the bot to connect to.
   */
  @Prop() botUrl!: string;

  /**
   * The text to display on the button.
   */
  @Prop() buttonText: string = "Chat";

  /**
   * Whether the chat widget is visible on load.
   */
  @Prop({ mutable: true }) visible: boolean = false;

  /**
   * The initial position of the chat widget on the screen.
   */
  @Prop({ mutable: true }) position: 'left' | 'center' | 'right' = 'right';

  /**
   * Whether the chat widget is initially expanded.
   */
  @Prop({ mutable: true }) expanded: boolean = false;

  @State() loaded: boolean = false;
  @State() error: string = "";

  componentWillLoad() {
    this.loaded = this.visible;
    if (!Build.isDev) {
      try {
        const url = new URL(this.botUrl);
        if (!allowedHosts.includes(url.host)) {
          this.error = `Invalid Bot URL: ${this.botUrl}`;
        }
      } catch {
        this.error = `Invalid Bot URL: ${this.botUrl}`;
      }
    }
  }

  load() {
    this.visible = !this.visible;
    this.loaded = true;
  }

  setPosition(position: 'left' | 'center' | 'right') {
    if (position === this.position) return;
    this.position = position;
  }

  toggleSize() {
    this.expanded = !this.expanded;
  }

  getPositionClasses() {
    const baseClasses = `fixed w-full sm:w-[450px] ${this.expanded ? 'h-5/6' : 'h-3/5'} bg-white border border-gray-200 shadow-lg rounded-lg overflow-hidden flex flex-col`;

    const positionClasses = {
      left: 'left-0 sm:left-5 bottom-0 sm:bottom-5',
      right: 'right-0 sm:right-5 bottom-0 sm:bottom-5',
      center: 'left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2'
    }[this.position];

    return `${baseClasses} ${positionClasses}`;
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
        {this.visible && (
          <div class={this.getPositionClasses()}>
            <div class="flex justify-between items-center px-2 py-2 border-b border-gray-100">
              <div class="flex gap-1">
                <button
                  class={{
                    'p-1.5 rounded-md transition-colors duration-200 hover:bg-gray-100': true,
                    'text-blue-600': this.position === 'left',
                    'text-gray-500': this.position !== 'left'
                  }}
                  onClick={() => this.setPosition('left')}
                  aria-label="Dock to left"
                  title="Dock to left"
                >
                  <ArrowLeftEndOnRectangleIcon/>
                </button>
                <button
                  class={{
                    'p-1.5 rounded-md transition-colors duration-200 hover:bg-gray-100': true,
                    'text-blue-600': this.position === 'center',
                    'text-gray-500': this.position !== 'center'
                  }}
                  onClick={() => this.setPosition('center')}
                  aria-label="Center"
                  title="Center"
                >
                  <ViewfinderCircleIcon/>
                </button>
                <button
                  class={{
                    'p-1.5 rounded-md transition-colors duration-200 hover:bg-gray-100': true,
                    'text-blue-600': this.position === 'right',
                    'text-gray-500': this.position !== 'right'
                  }}
                  onClick={() => this.setPosition('right')}
                  aria-label="Dock to right"
                  title="Dock to right"
                >
                  <ArrowRightEndOnRectangleIcon/>
                </button>
              </div>
              <div class="flex gap-1">
                <button
                  class="p-1.5 rounded-md transition-colors duration-200 hover:bg-gray-100 text-gray-500"
                  onClick={() => this.toggleSize()}
                  aria-label={this.expanded ? "Collapse" : "Expand"}
                  title={this.expanded ? "Collapse" : "Expand"}
                >
                  {this.expanded ? <ChevronDownIcon/> : <ChevronUpIcon/>}
                </button>
                <button
                  class="p-1.5 hover:bg-gray-100 rounded-md transition-colors duration-200 text-gray-500"
                  onClick={() => this.visible = false}
                  aria-label="Close"
                >
                  <XMarkIcon/>
                </button>
              </div>
            </div>
            {this.loaded && (
              <iframe
                class="flex-grow w-full border-none iframe-placeholder"
                src={this.botUrl}
              ></iframe>
            )}
          </div>
        )}
      </Host>
    );
  }
}
