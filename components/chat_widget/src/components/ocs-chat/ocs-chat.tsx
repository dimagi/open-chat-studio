import { Component, Host, h, Prop, State, Element } from '@stencil/core';
import {
  XMarkIcon,
  GripDotsVerticalIcon, PlusIcon, ArrowsPointingOutIcon, ArrowsPointingInIcon,
} from './heroicons';
import { renderMarkdownSync as renderMarkdownComplete } from '../../utils/markdown';
import { getCSRFToken } from '../../utils/cookies';
import { varToPixels } from '../../utils/utils';

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

interface PointerEvent {
  clientX: number;
  clientY: number;
}

interface SessionStorageData {
  sessionId?: string;
  messages: ChatMessage[];
}

@Component({
  tag: 'open-chat-studio-widget',
  styleUrl: 'ocs-chat.css',
  shadow: true,
})
export class OcsChat {

  private static readonly TASK_POLLING_MAX_ATTEMPTS = 30;
  private static readonly TASK_POLLING_INTERVAL_MS = 1000;
  private static readonly MESSAGE_POLLING_INTERVAL_MS = 30000;

  private static readonly SCROLL_DELAY_MS = 100;
  private static readonly FOCUS_DELAY_MS = 100;

  private static readonly MOBILE_BREAKPOINT = 640;
  private static readonly WINDOW_MARGIN = 20;

  private static readonly LOCALSTORAGE_TEST_KEY = '__ocs_test__';

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
  @Prop() buttonText?: string;

  /**
   * URL of the icon to display on the button. If not provided, uses the default OCS logo.
   */
  @Prop() iconUrl?: string;

  /**
   * The shape of the chat button. 'round' makes it circular, 'square' keeps it rectangular.
   */
  @Prop() buttonShape: 'round' | 'square' = 'square';

  /**
   * The text to place in the header.
   */
  @Prop() headerText: '';

  /**
   * Whether the chat widget is visible on load.
   */
  @Prop({ mutable: true }) visible: boolean = false;

  /**
   * The initial position of the chat widget on the screen.
   */
  @Prop({ mutable: true }) position: 'left' | 'center' | 'right' = 'right';

  /**
   * Welcome messages to display above starter questions (JSON array of strings)
   */
  @Prop() welcomeMessages?: string;

  /**
   * Array of starter questions that users can click to send (JSON array of strings)
   */
  @Prop() starterQuestions?: string;

  /**
  * Used to associate chat sessions with a specific user across multiple visits/sessions
   */
  @Prop() userId?: string;
  /**
   * Display name for the user.
   */
  @Prop() userName?: string;
  /**
   * Whether to persist session data to local storage to allow resuming previous conversations after page reload.
   */
  @Prop() persistentSession: boolean = true;

  /**
   * Minutes since the most recent message after which the session data in local storage will expire. Set this to
   * `0` to never expire.
   */
  @Prop() persistentSessionExpire: number = 60 * 24;

  /**
   * Allow the user to make the chat window full screen.
   */
  @Prop() allowFullScreen: boolean = true;

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
  @State() isDragging: boolean = false;
  @State() dragOffset: { x: number; y: number } = { x: 0, y: 0 };
  @State() windowPosition: { x: number; y: number } = { x: 0, y: 0 };
  @State() fullscreenPosition: { x: number } = { x: 0 };
  @State() showStarterQuestions: boolean = true;
  @State() parsedWelcomeMessages: string[] = [];
  @State() parsedStarterQuestions: string[] = [];
  @State() generatedUserId?: string;
  @State() isFullscreen: boolean = false;

  private messageListRef?: HTMLDivElement;
  private textareaRef?: HTMLTextAreaElement;
  private chatWindowRef?: HTMLDivElement;
  private chatWindowHeight: number = 600;
  private chatWindowWidth: number = 450;
  private chatWindowFullscreenWidth: number = 1024;
  @Element() host: HTMLElement;


  componentWillLoad() {
    this.loaded = this.visible;
    if (!this.chatbotId) {
      this.error = 'Chatbot ID is required';
      return;
    }
    // Always try to load existing session if localStorage is available
    if (this.persistentSession && this.isLocalStorageAvailable()) {
      const { sessionId, messages } = this.loadSessionFromStorage();
      if (sessionId && messages) {
        this.sessionId = sessionId;
        this.messages = messages;
        this.showStarterQuestions = messages.length === 0;
      }
    }
    this.parseWelcomeMessages();
    this.parseStarterQuestions();
  }

  componentDidLoad() {
    // Only auto-start session if we don't have an existing one
    if (this.visible && !this.sessionId) {
      this.startSession();
    } else if (this.visible && this.sessionId) {
      // Resume polling for existing session
      this.startPolling();
    }
    const computedStyle = getComputedStyle(this.host);
    const windowHeightVar = computedStyle.getPropertyValue('--chat-window-height');
    const windowWidthVar = computedStyle.getPropertyValue('--chat-window-width');
    const fullscreenWidthVar = computedStyle.getPropertyValue('--chat-window-fullscreen-width');
    this.chatWindowHeight = varToPixels(windowHeightVar, window.innerHeight, this.chatWindowHeight);
    this.chatWindowWidth = varToPixels(windowWidthVar, window.innerWidth, this.chatWindowWidth);
    this.chatWindowFullscreenWidth = varToPixels(fullscreenWidthVar, window.innerWidth, this.chatWindowFullscreenWidth);
    this.initializePosition();
    window.addEventListener('resize', this.handleWindowResize);
  }

  disconnectedCallback() {
    this.cleanup();
    this.removeEventListeners();
    window.removeEventListener('resize', this.handleWindowResize);
  }

  private parseJSONProp(propValue: string | undefined, propName: string): string[] {
    try {
      if (propValue) {
        try {
          return JSON.parse(propValue);
        } catch {
          const fixedValue = propValue.replace(/'/g, '"');
          return JSON.parse(fixedValue);
        }
      }
    } catch (error) {
      console.warn(`Failed to parse ${propName}:`, error);
    }
    return [];
  }

  private parseWelcomeMessages() {
    this.parsedWelcomeMessages = this.parseJSONProp(this.welcomeMessages, 'welcome messages');
  }

  private parseStarterQuestions() {
    this.parsedStarterQuestions = this.parseJSONProp(this.starterQuestions, 'starter questions');
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

  private getApiHeaders(): Record<string, string> {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    };

    const csrfToken = getCSRFToken(this.getApiBaseUrl());
    if (csrfToken) {
      headers['X-CSRFToken'] = csrfToken;
    }

    return headers;
  }

  private async startSession(): Promise<void> {
    try {
      this.isLoading = true;
      this.error = '';

      const userId = this.getOrGenerateUserId();

      const requestBody: any = {
        chatbot_id: this.chatbotId,
        session_data: {
          source: 'widget',
          page_url: window.location.href
        },
        participant_remote_id: userId
      };

      if (this.userName) {
        requestBody.participant_name = this.userName;
      }

      const response = await fetch(`${this.getApiBaseUrl()}/api/chat/start/`, {
        method: 'POST',
        headers: this.getApiHeaders(),
        body: JSON.stringify(requestBody)
      });

      if (!response.ok) {
        throw new Error(`Failed to start session: ${response.statusText}`);
      }

      const data: ChatStartSessionResponse = await response.json();
      this.sessionId = data.session_id;
      this.saveSessionToStorage();

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

    // Hide starter questions on any user interaction
    this.showStarterQuestions = false;

    try {
      // If this is the first user message and there are welcome messages,
      // add them to chat history as assistant messages
      if (this.messages.length === 0 && this.parsedWelcomeMessages.length > 0) {
        const now = new Date();
        const welcomeMessages: ChatMessage[] = this.parsedWelcomeMessages.map((welcomeMsg, index) => ({
          created_at: new Date(now.getTime() - (this.parsedWelcomeMessages.length - index) * 1000).toISOString(),
          role: 'assistant' as const,
          content: welcomeMsg,
          attachments: []
        }));
        this.messages = [...this.messages, ...welcomeMessages];
      }
      // Add user message immediately
      const userMessage: ChatMessage = {
        created_at: new Date().toISOString(),
        role: 'user',
        content: message.trim(),
        attachments: []
      };
      this.messages = [...this.messages, userMessage];
      this.saveSessionToStorage();
      this.messageInput = '';
      this.scrollToBottom();

      // Start typing indicator - it will stay on during task polling
      this.isTyping = true;

      const response = await fetch(`${this.getApiBaseUrl()}/api/chat/${this.sessionId}/message/`, {
        method: 'POST',
        headers: this.getApiHeaders(),
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

  private handleStarterQuestionClick(question: string): void {
    this.sendMessage(question);
  }

  private async pollTaskResponse(taskId: string): Promise<void> {
    if (!this.sessionId) return;

    // Stop message polling while task polling is active
    this.isTaskPolling = true;
    this.pauseMessagePolling();

    let attempts = 0;

    const poll = async (): Promise<void> => {
      try {
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
          this.saveSessionToStorage();
          this.scrollToBottom();
          // Task polling complete, clear typing indicator and resume message polling
          this.isTyping = false;
          this.isTaskPolling = false;
          this.resumeMessagePolling();
          this.focusInput();
          return;
        }

        if (data.status === 'processing' && attempts < OcsChat.TASK_POLLING_MAX_ATTEMPTS) {
          attempts++;
          setTimeout(poll, OcsChat.TASK_POLLING_INTERVAL_MS);
        } else if (attempts >= OcsChat.TASK_POLLING_MAX_ATTEMPTS) {
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
    }, OcsChat.MESSAGE_POLLING_INTERVAL_MS);
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
      if (this.messages && this.messages.length > 0) {
        url.searchParams.set('since', this.messages.at(-1).created_at);
      }

      const response = await fetch(url.toString());

      if (!response.ok) return; // Silently fail for polling

      const data: ChatPollResponse = await response.json();

      if (data.messages.length > 0) {
        this.messages = [...this.messages, ...data.messages];
        this.saveSessionToStorage();
        this.scrollToBottom();
        this.focusInput();
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
    }, OcsChat.SCROLL_DELAY_MS);
  }

  private focusInput(): void {
    setTimeout(() => {
      if (this.textareaRef && !this.isTyping) {
        this.textareaRef.focus();
      }
    }, OcsChat.FOCUS_DELAY_MS);
  }

  private handleKeyPress(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.sendMessage(this.messageInput);
    }
  }

  private handleInputChange(event: Event): void {
    this.messageInput = (event.target as HTMLTextAreaElement).value;
    // Hide starter questions when user starts typing
    if (this.messageInput.trim().length > 0) {
      this.showStarterQuestions = false;
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

  getPositionClasses() {
    if (this.isFullscreen) {
      return 'chat-window-fullscreen';
    }
    const baseClasses = 'chat-window-normal';
    const draggingClass = this.isDragging ? ' chat-window-dragging' : '';
    return baseClasses + draggingClass;
  }

  private getFullscreenBounds() {
    const windowWidth = window.innerWidth;
    const actualChatWidth = Math.min(windowWidth, this.chatWindowFullscreenWidth);
    const centeredX = (windowWidth - actualChatWidth) / 2;
    const maxOffset = (windowWidth - actualChatWidth) / 2;

    return { windowWidth, actualChatWidth, centeredX, maxOffset };
  }

  getPositionStyles() {
    if (this.isFullscreen) {
      const { centeredX } = this.getFullscreenBounds();
      const finalX = centeredX + this.fullscreenPosition.x;

      return {
        left: `${finalX}px`,
        top: '0px',
        transform: 'none',
      };
    }
    return {
      left: `${this.windowPosition.x}px`,
      top: `${this.windowPosition.y}px`,
    };
  }

  private initializePosition(): void {
    const windowWidth = window.innerWidth;
    const windowHeight = window.innerHeight;
    const chatWidth = windowWidth < OcsChat.MOBILE_BREAKPOINT ? windowWidth : this.chatWindowWidth;
    const isMobile = windowWidth < OcsChat.MOBILE_BREAKPOINT;

    if (isMobile) {
      this.windowPosition = { x: 0, y: 0 };
      return;
    }

    switch (this.position) {
      case 'left':
        this.windowPosition = {
          x: OcsChat.WINDOW_MARGIN,
          y: windowHeight - this.chatWindowHeight - OcsChat.WINDOW_MARGIN
        };
        break;
      case 'right':
        this.windowPosition = {
          x: windowWidth - chatWidth - OcsChat.WINDOW_MARGIN,
          y: windowHeight - this.chatWindowHeight - OcsChat.WINDOW_MARGIN
        };
        break;
      case 'center':
        this.windowPosition = {
          x: (windowWidth - chatWidth) / 2,
          y: (windowHeight - this.chatWindowHeight) / 2
        };
        break;
    }
  }

  private getPointerCoordinates(event: MouseEvent | TouchEvent): PointerEvent | null {
    if (event instanceof MouseEvent) {
      return { clientX: event.clientX, clientY: event.clientY };
    } else if (event instanceof TouchEvent && event.touches.length === 1) {
      const touch = event.touches[0];
      return { clientX: touch.clientX, clientY: touch.clientY };
    }
    return null;
  }

  private startDrag(pointer: PointerEvent): void {
    if (!this.chatWindowRef) return;

    this.isDragging = true;

    if (this.isFullscreen) {
      // For fullscreen, track relative to current position
      this.dragOffset = {
        x: pointer.clientX,
        y: pointer.clientY
      };
    } else {
      const rect = this.chatWindowRef.getBoundingClientRect();
      this.dragOffset = {
        x: pointer.clientX - rect.left,
        y: pointer.clientY - rect.top
      };
    }
  }

  private updateDragPosition(pointer: PointerEvent): void {
    if (!this.isDragging) return;

    if (this.isFullscreen) {
      // In fullscreen, only allow horizontal dragging
      const { maxOffset } = this.getFullscreenBounds();

      const deltaX = pointer.clientX - this.dragOffset.x;
      this.fullscreenPosition = {
        x: Math.max(-maxOffset, Math.min(maxOffset, deltaX))
      };
    } else {
      const newX = pointer.clientX - this.dragOffset.x;
      const newY = pointer.clientY - this.dragOffset.y;

      // Constrain chatbox to window
      const windowWidth = window.innerWidth;
      const windowHeight = window.innerHeight;
      const chatWidth = windowWidth < OcsChat.MOBILE_BREAKPOINT ? windowWidth : this.chatWindowWidth;
      const chatHeight = this.chatWindowRef.offsetHeight;

      this.windowPosition = {
        x: Math.max(0, Math.min(newX, windowWidth - chatWidth)),
        y: Math.max(0, Math.min(newY, windowHeight - chatHeight))
      };
    }
  }

  private endDrag(): void {
    this.isDragging = false;
    this.removeEventListeners();
  }

  private addEventListeners(): void {
    document.addEventListener('mousemove', this.handleMouseMove);
    document.addEventListener('mouseup', this.handleMouseUp);
    document.addEventListener('touchmove', this.handleTouchMove, { passive: false });
    document.addEventListener('touchend', this.handleTouchEnd);
  }

  private removeEventListeners(): void {
    document.removeEventListener('mousemove', this.handleMouseMove);
    document.removeEventListener('mouseup', this.handleMouseUp);
    document.removeEventListener('touchmove', this.handleTouchMove);
    document.removeEventListener('touchend', this.handleTouchEnd);
  }

  private handleMouseDown = (event: MouseEvent): void => {
    if (!this.isFullscreen && window.innerWidth < OcsChat.MOBILE_BREAKPOINT) return;
    if ((event.target as HTMLElement).closest('button')) return;

    const pointer = this.getPointerCoordinates(event);
    if (!pointer) return;

    this.startDrag(pointer);
    this.addEventListeners();
    event.preventDefault();
  };

  private handleMouseMove = (event: MouseEvent): void => {
    const pointer = this.getPointerCoordinates(event);
    if (!pointer) return;

    this.updateDragPosition(pointer);
  };

  private handleMouseUp = (): void => {
    this.endDrag();
  };

  private handleTouchStart = (event: TouchEvent): void => {
    if ((event.target as HTMLElement).closest('button')) return;
    if (!this.chatWindowRef) return;

    const pointer = this.getPointerCoordinates(event);
    if (!pointer) return;

    this.startDrag(pointer);
    this.addEventListeners();
    event.preventDefault();
  };

  private handleTouchMove = (event: TouchEvent): void => {
    const pointer = this.getPointerCoordinates(event);
    if (!pointer) return;

    this.updateDragPosition(pointer);
    event.preventDefault();
  };

  private handleTouchEnd = (): void => {
    this.endDrag();
  };

  private handleWindowResize = (): void => {
    this.initializePosition();
  };

  private getDefaultIconUrl(): string {
    return `${this.getApiBaseUrl()}/static/images/favicons/favicon.svg`;
  }

  private getButtonClasses(): string {
    const hasText = this.buttonText && this.buttonText.trim();
    const baseClass = hasText ? 'chat-btn-text' : 'chat-btn-icon';
    const shapeClass = this.buttonShape === 'round' ? 'round' : '';
    return `${baseClass} ${shapeClass}`.trim();
  }

  private renderButton() {
    const hasText = this.buttonText && this.buttonText.trim();
    const hasCustomIcon = this.iconUrl && this.iconUrl.trim();
    const iconSrc = hasCustomIcon ? this.iconUrl : this.getDefaultIconUrl();
    const buttonClasses = this.getButtonClasses();

    if (hasText) {
      return (
        <button
          class={buttonClasses}
          onClick={() => this.load()}
          aria-label={`Open chat - ${this.buttonText}`}
          title={this.buttonText}
        >
          <img src={iconSrc} alt="" />
          <span>{this.buttonText}</span>
        </button>
      );
    } else {
      return (
        <button
          class={buttonClasses}
          onClick={() => this.load()}
          aria-label="Open chat"
          title="Open chat"
        >
          <img src={iconSrc} alt="Chat" />
        </button>
      );
    }
  }

  private getStorageKeys() {
    return {
      sessionId: `ocs-chat-session-${this.chatbotId}`,
      messages: `ocs-chat-messages-${this.chatbotId}`,
      lastActivity: `ocs-chat-activity-${this.chatbotId}`
    };
  }

  private saveSessionToStorage(): void {
    if (!this.persistentSession) {
      return
    }
    const keys = this.getStorageKeys();
    try {
      if (this.sessionId) {
        localStorage.setItem(keys.sessionId, this.sessionId);
        localStorage.setItem(keys.lastActivity, new Date().toISOString());
      }
      localStorage.setItem(keys.messages, JSON.stringify(this.messages));
    } catch (error) {
      console.warn('Failed to save chat session to localStorage:', error);
    }
  }

  private loadSessionFromStorage(): SessionStorageData {
    const keys = this.getStorageKeys();
    try {
      if (this.persistentSessionExpire > 0) {
        const lastActivity = localStorage.getItem(keys.lastActivity);
        if (lastActivity) {
          const lastActivityDate = new Date(lastActivity);
          const minutesSinceActivity = (Date.now() - lastActivityDate.getTime()) / (1000 * 60);
          if (minutesSinceActivity > this.persistentSessionExpire) {
            this.clearSessionStorage();
            return {messages: []};
          }
        }
      }

      const storedSessionId = localStorage.getItem(keys.sessionId);
      const sessionId = storedSessionId ? storedSessionId : undefined;

      const messagesJson = localStorage.getItem(keys.messages);
      let messages: ChatMessage[] = [];

      if (messagesJson) {
        try {
          const parsedMessages = JSON.parse(messagesJson);
          messages = Array.isArray(parsedMessages) ? parsedMessages : [];
        } catch (parseError) {
          console.warn('Failed to parse messages from localStorage:', parseError);
          messages = [];
        }
      }

      return { sessionId, messages };
    } catch (error) {
      // fall back to starting a new session
      console.warn('Failed to load chat session from localStorage, starting new session:', error);
      return { messages: [] };
    }
  }

  private getOrGenerateUserId(): string {
    if (this.userId) {
      return this.userId;
    }

    if (this.generatedUserId) {
      return this.generatedUserId;
    }

    const storageKey = `ocs-user-id`;
    const stored = localStorage.getItem(storageKey);
    if (stored) {
      this.generatedUserId = stored;
      return stored;
    }

    const array = new Uint8Array(9);
    window.crypto.getRandomValues(array);
    const randomString = Array.from(array, byte => byte.toString(36)).join('').substr(0, 9);
    const newUserId = `ocs:${Date.now()}_${randomString}`;
    this.generatedUserId = newUserId;
    localStorage.setItem(storageKey, newUserId);

    return newUserId;
  }

  private clearSessionStorage(): void {
    const keys = this.getStorageKeys();
    try {
      localStorage.removeItem(keys.sessionId);
      localStorage.removeItem(keys.messages);
      localStorage.removeItem(keys.lastActivity);
    } catch (error) {
      console.warn('Failed to clear chat session from localStorage:', error);
    }
  }

  private isLocalStorageAvailable(): boolean {
    try {
      localStorage.setItem(OcsChat.LOCALSTORAGE_TEST_KEY, 'test');
      localStorage.removeItem(OcsChat.LOCALSTORAGE_TEST_KEY);
      return true;
    } catch {
      return false;
    }
  }

  private async startNewChat(): Promise<void> {
    this.clearSessionStorage();
    this.sessionId = undefined;
    this.messages = [];
    this.showStarterQuestions = true;
    this.isTyping = false;
    this.error = '';
    this.cleanup();

    await this.startSession();
  }

  private toggleFullscreen(): void {
    this.isFullscreen = !this.isFullscreen;
    // Reset fullscreen position when toggling
    this.fullscreenPosition = { x: 0 };
  }

  render() {
    if (this.error) {
      return (
        <Host>
          <p class="error-message">{this.error}</p>
        </Host>
      );
    }

    return (
      <Host>
        {this.renderButton()}
        {this.visible && (
          <div
            ref={(el) => this.chatWindowRef = el}
            id="ocs-chat-window"
            class={this.getPositionClasses()}
            style={this.getPositionStyles()}
          >
            {/* Header */}
            <div
              class={`chat-header ${this.isDragging ? 'chat-header-dragging' : 'chat-header-draggable'}`}
              onMouseDown={this.handleMouseDown}
              onTouchStart={this.handleTouchStart}
            >
              {/* Drag indicator */}
              <div class="drag-indicator">
                <div class="drag-dots header-button">
                  <GripDotsVerticalIcon/>
                </div>
              </div>
              <div class="header-text">{this.headerText}</div>
              <div class="header-buttons">
                {/* Fullscreen toggle button */}
                {this.allowFullScreen && <button
                  class="header-button fullscreen-button"
                  onClick={() => this.toggleFullscreen()}
                  title={this.isFullscreen ? "Exit fullscreen" : "Enter fullscreen"}
                  aria-label={this.isFullscreen ? "Exit fullscreen" : "Enter fullscreen"}
                >
                  {this.isFullscreen ? <ArrowsPointingInIcon/> : <ArrowsPointingOutIcon/>}
                </button>}
                {/* New Chat button */}
                {this.sessionId && this.messages.length > 0 && (
                  <button
                    class="header-button"
                    onClick={() => this.startNewChat()}
                    title="Start new chat"
                    aria-label="Start new chat"
                  >
                    <PlusIcon/>
                  </button>
                )}
                <button
                  class="header-button"
                  onClick={() => this.visible = false}
                  aria-label="Close"
                >
                  <XMarkIcon/>
                </button>
              </div>
            </div>

            {/* Chat Content */}
            <div class="chat-content">
              {/* Loading State */}
              {this.isLoading && !this.sessionId && (
                <div class="loading-container">
                  <div class="loading-spinner"></div>
                  <span class="loading-text">Starting chat...</span>
                </div>
              )}

              {/* Messages */}
              {this.sessionId && (
                <div
                  ref={(el) => this.messageListRef = el}
                  class="messages-container"
                >
                  {this.messages.length === 0 && !this.isTyping && this.parsedWelcomeMessages.length > 0 && (
                    <div class="welcome-messages">
                      {/* Welcome Messages */}
                      {this.parsedWelcomeMessages.map((message, index) => (
                        <div key={`welcome-${index}`} class="message-row message-row-assistant">
                          <div class="message-bubble message-bubble-assistant">
                            <div
                              class="chat-markdown"
                              innerHTML={renderMarkdownComplete(message)}
                            ></div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                  {/* Regular Chat Messages */}
                  {this.messages.map((message, index) => (
                    <div
                      key={index}
                      class={`message-row ${
                        message.role === 'user' ? 'message-row-user' : 'message-row-assistant'
                      }`}
                    >
                      <div
                        class={`message-bubble ${
                          message.role === 'user'
                            ? 'message-bubble-user'
                            : message.role === 'assistant'
                            ? 'message-bubble-assistant'
                            : 'message-bubble-system'
                        }`}
                      >
                        <div
                          class="chat-markdown"
                          innerHTML={renderMarkdownComplete(message.content)}
                        ></div>
                        {message.attachments && message.attachments.length > 0 && (
                          <div class="message-attachments">
                            {message.attachments.map((attachment, attachmentIndex) => (
                              <a
                                key={attachmentIndex}
                                href={attachment.content_url}
                                target="_blank"
                                rel="noopener noreferrer"
                                class="attachment-link"
                              >
                                📎 {attachment.name}
                              </a>
                            ))}
                          </div>
                        )}
                        <div class="message-timestamp">
                          {this.formatTime(message.created_at)}
                        </div>
                      </div>
                    </div>
                  ))}
                  {/* Typing Indicator */}
                  {this.isTyping && (
                    <div>
                      <div class="typing-indicator">
                        <div class="typing-progress"></div>
                      </div>
                      <div class="typing-text">
                        <span>Preparing response</span>
                        <span class="typing-dots"></span>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Starter Questions */}
              {this.sessionId && this.showStarterQuestions && this.messages.length === 0 && !this.isTyping && (
                <div class="starter-questions">
                  {this.parsedStarterQuestions.map((question, index) => (
                    <div key={`starter-${index}`} class="starter-question-row">
                      <button
                        class="starter-question"
                        onClick={() => this.handleStarterQuestionClick(question)}
                      >
                        {question}
                      </button>
                    </div>
                  ))}
                </div>
              )}

              {/* Input Area */}
              {this.sessionId && (
                <div class="input-area">
                  <div class="input-container">
                    <textarea
                      ref={(el) => this.textareaRef = el}
                      class="message-textarea"
                      rows={1}
                      placeholder="Type your message..."
                      value={this.messageInput}
                      onInput={(e) => this.handleInputChange(e)}
                      onKeyPress={(e) => this.handleKeyPress(e)}
                      disabled={this.isTyping}
                    ></textarea>
                    <button
                      class={`send-button ${
                        !this.isTyping && !!this.messageInput.trim()
                          ? 'send-button-enabled'
                          : 'send-button-disabled'
                      }`}
                      onClick={() => this.sendMessage(this.messageInput)}
                      disabled={this.isTyping || !this.messageInput.trim()}
                    >
                      Send
                    </button>
                  </div>
                </div>
              )}
              <div class="flex items-center justify-center text-[0.8em] font-light w-full text-slate-500 py-[2px]">
                <p>Powered by <a class="underline" href="https://www.dimagi.com" target="_blank">Dimagi</a></p>
              </div>
            </div>
          </div>
        )}
      </Host>
    );
  }
}
