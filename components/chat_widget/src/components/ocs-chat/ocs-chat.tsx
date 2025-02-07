import { Component, Host, h, Prop, State, Build } from '@stencil/core';
import {ArrowLeftEndOnRectangleIcon, ArrowRightEndOnRectangleIcon, ViewfinderCircleIcon, XMarkIcon} from './heroicons';

const allowedHosts = ["chatbots.dimagi.com"];

@Component({
  tag: 'open-chat-studio-widget',
  styleUrl: 'ocs-chat.css',
  shadow: true,
})
export class OcsChat {
  @Prop() boturl!: string;
  @Prop() buttonText: string = "Chat";
  @Prop({ mutable: true }) visible: boolean = false;
  @Prop({ mutable: true }) anchorPosition: 'left' | 'center' | 'right' = 'right';

  @State() loaded: boolean = false;
  @State() error: string = "";

  componentWillLoad() {
    this.loaded = this.visible;
    if (!Build.isDev) {
      try {
        const url = new URL(this.boturl);
        if (!allowedHosts.includes(url.host)) {
          this.error = `Invalid Bot URL: ${this.boturl}`;
        }
      } catch {
        this.error = `Invalid Bot URL: ${this.boturl}`;
      }
    }
  }

  load() {
    this.visible = !this.visible;
    this.loaded = true;
  }

  setPosition(position: 'left' | 'center' | 'right') {
    if (position === this.anchorPosition) return;
    this.anchorPosition = position;
  }

  getPositionClasses() {
    const baseClasses = 'fixed w-[450px] h-5/6 bg-white border border-gray-200 shadow-lg rounded-lg overflow-hidden flex flex-col';

    // Position-specific classes
    const positionClasses = {
      left: 'left-5 bottom-5',
      right: 'right-5 bottom-5',
      center: 'left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2'
    }[this.anchorPosition];

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
                    'text-blue-600': this.anchorPosition === 'left',
                    'text-gray-500': this.anchorPosition !== 'left'
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
                    'text-blue-600': this.anchorPosition === 'center',
                    'text-gray-500': this.anchorPosition !== 'center'
                  }}
                  onClick={() => this.setPosition('center')}
                  aria-label="Center"
                  title="Center"
                >
                  <ViewfinderCircleIcon />
                </button>
                <button
                  class={{
                    'p-1.5 rounded-md transition-colors duration-200 hover:bg-gray-100': true,
                    'text-blue-600': this.anchorPosition === 'right',
                    'text-gray-500': this.anchorPosition !== 'right'
                  }}
                  onClick={() => this.setPosition('right')}
                  aria-label="Dock to right"
                  title="Dock to right"
                >
                 <ArrowRightEndOnRectangleIcon />
                </button>
              </div>
              <button
                class="p-1.5 hover:bg-gray-100 rounded-md transition-colors duration-200 text-gray-500"
                onClick={() => this.visible = false}
                aria-label="Close"
              >
                <XMarkIcon />
              </button>
            </div>
            {this.loaded && (
              <iframe
                class="flex-grow w-full border-none iframe-placeholder"
                src={this.boturl}
              ></iframe>
            )}
          </div>
        )}
      </Host>
    );
  }
}
