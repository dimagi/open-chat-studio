import { Component, Host, h, Prop, State, Build } from '@stencil/core';

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

  @State() loaded: boolean = false;
  @State() error: string = "";
  @State() anchorPosition: 'left' | 'center' | 'right' = 'right';

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
                  <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5"
                       stroke="currentColor" class="size-6">
                    <path stroke-linecap="round" stroke-linejoin="round"
                          d="M15.75 9V5.25A2.25 2.25 0 0 0 13.5 3h-6a2.25 2.25 0 0 0-2.25 2.25v13.5A2.25 2.25 0 0 0 7.5 21h6a2.25 2.25 0 0 0 2.25-2.25V15M12 9l-3 3m0 0 3 3m-3-3h12.75"/>
                  </svg>
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
                  <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5"
                       stroke="currentColor" class="size-6">
                    <path stroke-linecap="round" stroke-linejoin="round"
                          d="M7.5 3.75H6A2.25 2.25 0 0 0 3.75 6v1.5M16.5 3.75H18A2.25 2.25 0 0 1 20.25 6v1.5m0 9V18A2.25 2.25 0 0 1 18 20.25h-1.5m-9 0H6A2.25 2.25 0 0 1 3.75 18v-1.5M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z"/>
                  </svg>

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
                  <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5"
                       stroke="currentColor" class="size-6">
                    <path stroke-linecap="round" stroke-linejoin="round"
                          d="M8.25 9V5.25A2.25 2.25 0 0 1 10.5 3h6a2.25 2.25 0 0 1 2.25 2.25v13.5A2.25 2.25 0 0 1 16.5 21h-6a2.25 2.25 0 0 1-2.25-2.25V15M12 9l3 3m0 0-3 3m3-3H2.25"/>
                  </svg>

                </button>
              </div>
              <button
                class="p-1.5 hover:bg-gray-100 rounded-md transition-colors duration-200 text-gray-500"
                onClick={() => this.visible = false}
                aria-label="Close"
              >
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5"
                     stroke="currentColor" class="size-6">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12"/>
                </svg>

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
