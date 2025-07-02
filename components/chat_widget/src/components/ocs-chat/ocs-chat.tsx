import { Component, Host, h, Prop, State } from '@stencil/core';
import {
  ArrowLeftEndOnRectangleIcon,
  ArrowRightEndOnRectangleIcon,
  ArrowDownOnSquareIcon,
  ViewfinderCircleIcon,
  XMarkIcon,
  ChevronDownIcon,
  ChevronUpIcon,
} from './heroicons';

interface ChatMessage {
  created_at: string;
  role: 'system' | 'user' | 'assistant';
  content: string;
  metadata?: any;
  attachments?: ChatAttachment[];
}

interface ChatAttachment {
  name: string;
  content_type: string;
  size: number;
  content_url: string;
}

interface ChatStartSessionResponse {
  session_id: string;
  chatbot: any;
  participant: any;
  seed_message_task_id?: string;
}

interface ChatSendMessageResponse {
  task_id: string;
  status: 'processing' | 'completed' | 'error';
  error?: string;
}

interface ChatTaskPollResponse {
  message?: ChatMessage;
  status: 'processing' | 'complete';
  error?: string;
}

interface ChatPollResponse {
  messages: ChatMessage[];
  has_more: boolean;
  session_status: 'active' | 'ended';
}

@Component({
  tag: 'open-chat-studio-widget',
  styleUrl: 'ocs-chat.css',
  shadow: true,
})
export class OcsChat {

  /**
   * The ID of the chatbot to connect to.
   */
  @Prop() chatbotId!: string;

  /**
   * The base URL for the API (defaults to current origin).
   */
  @Prop() apiBaseUrl?: string = "https://chatbots.dimagi.com";

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
  @State() messages: ChatMessage[] = [];
  @State() sessionId?: string;
  @State() isLoading: boolean = false;
  @State() isTyping: boolean = false;
  @State() messageInput: string = "";
  @State() pollingInterval?: any;
  @State() lastPollTime?: Date;
  @State() isTaskPolling: boolean = false;

  private messageListRef?: HTMLDivElement;

  componentWillLoad() {
    this.loaded = this.visible;
    if (!this.chatbotId) {
      this.error = 'Chatbot ID is required';
    }
  }

  componentDidLoad() {
    // Auto-start session if visible on load
    if (this.visible && !this.sessionId) {
      this.startSession();
    }
  }

  disconnectedCallback() {
    this.cleanup();
  }

  private cleanup() {
    if (this.pollingInterval) {
      clearInterval(this.pollingInterval);
      this.pollingInterval = undefined;
    }
    this.isTaskPolling = false;
  }

  private getApiBaseUrl(): string {
    return this.apiBaseUrl || window.location.origin;
  }

  private async startSession(): Promise<void> {
    try {
      this.isLoading = true;
      this.error = '';

      const response = await fetch(`${this.getApiBaseUrl()}/api/chat/start/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          chatbot_id: this.chatbotId,
          session_data: {
            source: 'widget',
            page_url: window.location.href
          }
        })
      });

      if (!response.ok) {
        throw new Error(`Failed to start session: ${response.statusText}`);
      }

      const data: ChatStartSessionResponse = await response.json();
      this.sessionId = data.session_id;

      // Handle seed message if present
      if (data.seed_message_task_id) {
        this.isTyping = true;  // Show typing indicator for seed message
        await this.pollTaskResponse(data.seed_message_task_id);
      }

      // Start polling for messages
      this.startPolling();
    } catch (error) {
      this.error = error instanceof Error ? error.message : 'Failed to start chat session';
    } finally {
      this.isLoading = false;
    }
  }

  private async sendMessage(message: string): Promise<void> {
    if (!this.sessionId || !message.trim()) return;

    try {
      // Add user message immediately
      const userMessage: ChatMessage = {
        created_at: new Date().toISOString(),
        role: 'user',
        content: message.trim(),
        attachments: []
      };
      this.messages = [...this.messages, userMessage];
      this.messageInput = '';
      this.scrollToBottom();

      // Start typing indicator - it will stay on during task polling
      this.isTyping = true;

      const response = await fetch(`${this.getApiBaseUrl()}/api/chat/${this.sessionId}/message/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ message: message.trim() })
      });

      if (!response.ok) {
        throw new Error(`Failed to send message: ${response.statusText}`);
      }

      const data: ChatSendMessageResponse = await response.json();

      if (data.status === 'error') {
        throw new Error(data.error || 'Failed to send message');
      }

      // Poll for the response - typing indicator will be managed in pollTaskResponse
      await this.pollTaskResponse(data.task_id);
    } catch (error) {
      this.error = error instanceof Error ? error.message : 'Failed to send message';
      // Clear typing indicator on error
      this.isTyping = false;
    }
  }

  private async pollTaskResponse(taskId: string): Promise<void> {
    if (!this.sessionId) return;

    // Stop message polling while task polling is active
    this.isTaskPolling = true;
    this.pauseMessagePolling();

    const maxAttempts = 30; // 30 seconds max
    let attempts = 0;

    const poll = async (): Promise<void> => {
      try {
        console.log("polling for task", taskId);
        const response = await fetch(`${this.getApiBaseUrl()}/api/chat/${this.sessionId}/${taskId}/poll/`);

        if (!response.ok) {
          throw new Error(`Failed to poll task: ${response.statusText}`);
        }

        const data: ChatTaskPollResponse = await response.json();

        if (data.error) {
          throw new Error(data.error);
        }

        if (data.status === 'complete' && data.message) {
          this.messages = [...this.messages, data.message];
          this.scrollToBottom();
          // Task polling complete, clear typing indicator and resume message polling
          this.isTyping = false;
          this.isTaskPolling = false;
          this.resumeMessagePolling();
          return;
        }

        if (data.status === 'processing' && attempts < maxAttempts) {
          attempts++;
          setTimeout(poll, 1000);
        } else if (attempts >= maxAttempts) {
          // Task polling timed out, clear typing indicator and resume message polling
          this.isTyping = false;
          this.isTaskPolling = false;
          this.resumeMessagePolling();
        }
      } catch (error) {
        this.error = error instanceof Error ? error.message : 'Failed to get response';
        // Error in task polling, clear typing indicator and resume message polling
        this.isTyping = false;
        this.isTaskPolling = false;
        this.resumeMessagePolling();
      }
    };

    await poll();
  }

  private startPolling(): void {
    if (this.pollingInterval) return;

    this.pollingInterval = setInterval(async () => {
      // Only poll for messages if not currently polling for a task
      if (!this.isTaskPolling) {
        await this.pollForMessages();
      }
    }, 30000); // Poll every 30 seconds
  }

  private pauseMessagePolling(): void {
    if (this.pollingInterval) {
      clearInterval(this.pollingInterval);
      this.pollingInterval = undefined;
    }
  }

  private resumeMessagePolling(): void {
    // Resume message polling after task polling is complete
    this.startPolling();
  }

  private async pollForMessages(): Promise<void> {
    if (!this.sessionId) return;

    try {
      const url = new URL(`${this.getApiBaseUrl()}/api/chat/${this.sessionId}/poll/`);
      if (this.messages) {
        console.log(`Messages from ${JSON.stringify(this.messages)}`);
        url.searchParams.set('since', this.messages.at(-1).created_at);
      }

      const response = await fetch(url.toString());

      if (!response.ok) return; // Silently fail for polling

      const data: ChatPollResponse = await response.json();

      if (data.messages.length > 0) {
        this.messages = [...this.messages, ...data.messages];
        this.scrollToBottom();
      }

      this.lastPollTime = new Date();
    } catch (error) {
      // Silently fail for polling
    }
  }

  private clearError() {
    this.error = '';
  }


  private scrollToBottom(): void {
    setTimeout(() => {
      if (this.messageListRef) {
        this.messageListRef.scrollTop = this.messageListRef.scrollHeight;
      }
    }, 100);
  }

  private handleKeyPress(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.sendMessage(this.messageInput);
    }
  }

  private formatTime(dateString: string): string {
    const date = new Date(dateString);
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  async load() {
    this.visible = !this.visible;
    this.loaded = true;

    if (this.visible && !this.sessionId) {
      this.clearError();
      await this.startSession();
    } else if (!this.visible) {
      // Don't reset session when closing, allow resume
    }
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
          <p class="text-red-500 p-2">{this.error}</p>
        </Host>
      );
    }

    return (
      <Host>
        <button class="btn" onClick={() => this.load()}>{this.buttonText}</button>
        {this.visible && (
          <div id="ocs-chat-window" class={this.getPositionClasses()}>
            {/* Header */}
            <div class="flex justify-between items-center px-2 py-2 border-b border-gray-100">
              <div class="flex gap-1">
                <button
                  class={{
                    'hidden sm:block p-1.5 rounded-md transition-colors duration-200 hover:bg-gray-100': true,
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
                  <span class="hidden sm:block"><ArrowRightEndOnRectangleIcon/></span>
                  <span class="sm:hidden"><ArrowDownOnSquareIcon/></span>
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

            {/* Chat Content */}
            <div class="flex flex-col flex-grow overflow-hidden">
              {/* Loading State */}
              {this.isLoading && !this.sessionId && (
                <div class="flex items-center justify-center flex-grow">
                  <div class="loading-spinner"></div>
                  <span class="ml-2 text-gray-500">Starting chat...</span>
                </div>
              )}

              {/* Messages */}
              {this.sessionId && (
                <div
                  ref={(el) => this.messageListRef = el}
                  class="flex-grow overflow-y-auto p-4 space-y-4"
                >
                  {this.messages.map((message, index) => (
                    <div
                      key={index}
                      class={{
                        'flex': true,
                        'justify-end': message.role === 'user',
                        'justify-start': message.role !== 'user'
                      }}
                    >
                      <div
                        class={{
                          'max-w-xs lg:max-w-md px-4 py-2 rounded-lg': true,
                          'bg-blue-500 text-white': message.role === 'user',
                          'bg-gray-200 text-gray-800': message.role === 'assistant',
                          'bg-gray-100 text-gray-600 text-sm': message.role === 'system'
                        }}
                      >
                        <div class="whitespace-pre-wrap">{message.content}</div>
                        {message.attachments && message.attachments.length > 0 && (
                          <div class="mt-2 space-y-1">
                            {message.attachments.map((attachment, attachmentIndex) => (
                              <a
                                key={attachmentIndex}
                                href={attachment.content_url}
                                target="_blank"
                                rel="noopener noreferrer"
                                class="block text-sm underline hover:no-underline"
                              >
                                📎 {attachment.name}
                              </a>
                            ))}
                          </div>
                        )}
                        <div class="text-xs opacity-70 mt-1">
                          {this.formatTime(message.created_at)}
                        </div>
                      </div>
                    </div>
                  ))}

                  {/* Typing Indicator */}
                  {this.isTyping && (
                    <div class="flex justify-start">
                      <div class="bg-gray-200 text-gray-800 max-w-xs lg:max-w-md px-2 py-2 rounded-lg">
                        <div class="flex items-center gap-0.5">
                          <span class="inline-block w-2 h-2 rounded-full bg-gray-400 animate-bounce"></span>
                          <span class="inline-block w-2 h-2 rounded-full bg-gray-400 animate-bounce" style={{animationDelay: '0.1s'}}></span>
                          <span class="inline-block w-2 h-2 rounded-full bg-gray-400 animate-bounce" style={{animationDelay: '0.2s'}}></span>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Input Area */}
              {this.sessionId && (
                <div class="border-t border-gray-200 p-4">
                  <div class="flex gap-2">
                    <textarea
                      class="flex-grow px-3 py-2 border border-gray-300 rounded-md resize-none focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                      rows={1}
                      placeholder="Type your message..."
                      value={this.messageInput}
                      onInput={(e) => this.messageInput = (e.target as HTMLTextAreaElement).value}
                      onKeyPress={(e) => this.handleKeyPress(e)}
                      disabled={this.isTyping}
                    ></textarea>
                    <button
                      class={{
                        'px-4 py-2 rounded-md font-medium transition-colors duration-200': true,
                        'bg-blue-500 hover:bg-blue-600 text-white': !this.isTyping && !!this.messageInput.trim(),
                        'bg-gray-300 text-gray-500 cursor-not-allowed': this.isTyping || !this.messageInput.trim()
                      }}
                      onClick={() => this.sendMessage(this.messageInput)}
                      disabled={this.isTyping || !this.messageInput.trim()}
                    >
                      Send
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </Host>
    );
  }
}
