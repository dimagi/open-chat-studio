// eslint-disable-next-line @typescript-eslint/no-unused-vars
import {Component, Host, h, Prop, State, Element, Watch, Env} from '@stencil/core';
import {
  XMarkIcon,
  GripDotsVerticalIcon, PlusWithCircleIcon, ArrowsPointingOutIcon, ArrowsPointingInIcon,
  PaperClipIcon, CheckDocumentIcon, XIcon
} from './heroicons';
import { renderMarkdownSync as renderMarkdownComplete } from '../../utils/markdown';
import { varToPixels } from '../../utils/utils';
import {TranslationStrings, TranslationManager, defaultTranslations} from '../../utils/translations';
import {
  ChatSessionService,
  ChatMessage,
  MessagePollingHandle,
  TaskPollingHandle
} from '../../services/chat-session-service';
import {
  FileAttachmentManager,
  SelectedFile
} from '../../services/file-attachment-manager';

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

  private static readonly TASK_POLLING_MAX_ATTEMPTS = 120;
  private static readonly TASK_POLLING_INTERVAL_MS = 1000;
  private static readonly MESSAGE_POLLING_INTERVAL_MS = 30000;

  private static readonly SCROLL_DELAY_MS = 100;
  private static readonly FOCUS_DELAY_MS = 100;

  private static readonly MOBILE_BREAKPOINT = 640;
  private static readonly WINDOW_MARGIN = 20;

  private static readonly LOCALSTORAGE_TEST_KEY = '__ocs_test__';

  private static readonly MAX_FILE_SIZE_MB = 50;
  private static readonly MAX_TOTAL_SIZE_MB = 50;
  private static readonly SUPPORTED_FILE_EXTENSIONS = ['.txt', '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.csv', '.jpg', '.jpeg',
    '.png', '.gif', '.bmp', '.webp', '.svg', '.mp4', '.mov', '.avi', '.mp3', '.wav' ];

  /**
   * The ID of the chatbot to connect to.
   */
  @Prop() chatbotId!: string;

  /**
   * The base URL for the API.
   */
  @Prop() apiBaseUrl?: string = "https://www.openchatstudio.com";

  /**
   * The text to display on the button.
   */
  @Prop() buttonText?: string;

  /**
   * URL of the icon to display on the button. If not provided, uses the default OCS logo.
   */
  @Prop() iconUrl?: string;

  /**
   * Authentication key for embedded channels
   */
  @Prop() embedKey?: string;

  /**
   * The shape of the chat button. 'round' makes it circular, 'square' keeps it rectangular.
   */
  @Prop() buttonShape: 'round' | 'square' = 'square';

  /**
   * The text to place in the header.
   */
  @Prop() headerText: '';

  /**
   * The message to display in the new chat confirmation dialog.
   */
  @Prop() newChatConfirmationMessage?: string = "Starting a new chat will clear your current conversation. Continue?";

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

  /**
   * Allow the user to attach files to their messages.
   */
  @Prop() allowAttachments: boolean = false;

  /**
   * The text to display while the assistant is typing/preparing a response.
   */
  @Prop() typingIndicatorText?: string = "Preparing response";

  /**
   * The language code for the widget UI (e.g., 'en', 'es', 'fr'). Defaults to en
   */
  @Prop() language?: string;

  @Prop() translationsUrl?: string;

  @State() error: string = "";
  @State() messages: ChatMessage[] = [];
  @State() sessionId?: string;
  @State() isLoading: boolean = false;
  @State() isTyping: boolean = false;
  @State() messageInput: string = "";
  @State() currentPollTaskId: string = "";
  @State() isDragging: boolean = false;
  @State() dragOffset: { x: number; y: number } = { x: 0, y: 0 };
  @State() windowPosition: { x: number; y: number } = { x: 0, y: 0 };
  @State() fullscreenPosition: { x: number } = { x: 0 };
  @State() parsedWelcomeMessages: string[] = [];
  @State() parsedStarterQuestions: string[] = [];
  @State() generatedUserId?: string;
  @State() isFullscreen: boolean = false;
  @State() showNewChatConfirmation: boolean = false;

  @State() selectedFiles: SelectedFile[] = [];
  @State() isUploadingFiles: boolean = false;

  translationManager: TranslationManager = new TranslationManager();

  private chatService?: ChatSessionService;
  private messagePollingHandle?: MessagePollingHandle;
  private taskPollingHandle?: TaskPollingHandle;
  private attachmentManager = new FileAttachmentManager({
    supportedExtensions: OcsChat.SUPPORTED_FILE_EXTENSIONS,
    maxFileSizeMb: OcsChat.MAX_FILE_SIZE_MB,
    maxTotalSizeMb: OcsChat.MAX_TOTAL_SIZE_MB,
  });
  private messageListRef?: HTMLDivElement;
  private textareaRef?: HTMLTextAreaElement;
  private chatWindowRef?: HTMLDivElement;
  private fileInputRef?: HTMLInputElement;
  private chatWindowHeight: number = 600;
  private chatWindowWidth: number = 450;
  private chatWindowFullscreenWidth: number = 1024;
  private positionInitialized: boolean = false;
  @Element() host: HTMLElement;


  async componentWillLoad() {
    if (!this.chatbotId) {
      this.error = 'Chatbot ID is required';
      return;
    }

    await this.initializeTranslations();

    // Always try to load existing session if localStorage is available
    if (this.persistentSession && this.isLocalStorageAvailable()) {
      const { sessionId, messages } = this.loadSessionFromStorage();
      if (sessionId && messages) {
        this.sessionId = sessionId;
        this.messages = messages;
      }
    }
    this.parseWelcomeMessages();
    this.parseStarterQuestions();
  }

  componentDidLoad() {
    const computedStyle = getComputedStyle(this.host);
    const windowHeightVar = computedStyle.getPropertyValue('--chat-window-height');
    const windowWidthVar = computedStyle.getPropertyValue('--chat-window-width');
    const fullscreenWidthVar = computedStyle.getPropertyValue('--chat-window-fullscreen-width');
    this.chatWindowHeight = varToPixels(windowHeightVar, window.innerHeight, this.chatWindowHeight);
    this.chatWindowWidth = varToPixels(windowWidthVar, window.innerWidth, this.chatWindowWidth);
    this.chatWindowFullscreenWidth = varToPixels(fullscreenWidthVar, window.innerWidth, this.chatWindowFullscreenWidth);
    if (this.visible) {
      this.initializePosition();
    }

    // Only auto-start session if we don't have an existing one
    if (this.visible && !this.sessionId) {
      this.startSession();
    } else if (this.visible && this.sessionId) {
      // Resume polling for existing session
      this.startMessagePolling();
    }
    window.addEventListener('resize', this.handleWindowResize);
  }

  disconnectedCallback() {
    this.cleanup();
    this.removeEventListeners();
    window.removeEventListener('resize', this.handleWindowResize);
  }

  private getChatService(): ChatSessionService {
    if (!this.chatService) {
      this.chatService = new ChatSessionService({
        apiBaseUrl: this.apiBaseUrl || 'https://www.openchatstudio.com',
        embedKey: this.embedKey,
        widgetVersion: Env.version,
        taskPollingIntervalMs: OcsChat.TASK_POLLING_INTERVAL_MS,
        taskPollingMaxAttempts: OcsChat.TASK_POLLING_MAX_ATTEMPTS,
        messagePollingIntervalMs: OcsChat.MESSAGE_POLLING_INTERVAL_MS,
      });
    }
    return this.chatService;
  }

  private addErrorMessage(errorText: string): void {
    const errorMessage: ChatMessage = {
      created_at: new Date().toISOString(),
      role: 'system',
      content: `**Error:** ${errorText}\nPlease try again.`,
      attachments: []
    };

    this.messages = [...this.messages, errorMessage];
    this.saveSessionToStorage();
    this.scrollToBottom();
  }

  private handleError(errorText: string): void {
    // show as system message
    this.addErrorMessage(errorText);

    // Clear any loading/typing states
    this.isLoading = false;
    this.isTyping = false;
    this.isUploadingFiles = false;
    this.currentPollTaskId = '';
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

  private async initializeTranslations() {
    let customTranslationsObj: Partial<TranslationStrings> | undefined;

    if (this.translationsUrl) {
        customTranslationsObj = await this.loadTranslationsFromUrl(this.translationsUrl);
    }
    this.translationManager = new TranslationManager(this.language, customTranslationsObj);
  }

  private async loadTranslationsFromUrl(url: string): Promise<Partial<TranslationStrings>> {
    try {
      const response = await fetch(url);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      const translations = await response.json();
      return translations as Partial<TranslationStrings>;
    } catch (error) {
      console.error('Error loading translations from URL:', error);
      return defaultTranslations
    }
  }

  private cleanup() {
    this.stopMessagePolling();
    if (this.taskPollingHandle) {
      this.taskPollingHandle.cancel();
      this.taskPollingHandle = undefined;
    }
    this.currentPollTaskId = '';
  }

  private async startSession(): Promise<void> {
    try {
      this.isLoading = true;

      const userId = this.getOrGenerateUserId();

      const requestBody: Record<string, unknown> = {
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

      const data = await this.getChatService().startSession(requestBody);
      this.sessionId = data.session_id;
      this.saveSessionToStorage();

      // Handle seed message if present
      if (data.seed_message_task_id) {
        this.startTaskPolling(data.seed_message_task_id);
      } else {
        this.startMessagePolling();
      }
    } catch (_error) {
      this.handleError('Failed to start chat session');
    } finally {
      this.isLoading = false;
    }
  }

  private async uploadFiles(): Promise<number[]> {
    if (this.selectedFiles.length === 0 || !this.sessionId || !this.allowAttachments) {
      return [];
    }

    this.isUploadingFiles = true;
    try {
      const uploadResult = await this.attachmentManager.uploadPendingFiles(this.selectedFiles, {
        apiBaseUrl: this.apiBaseUrl || 'https://www.openchatstudio.com',
        sessionId: this.sessionId,
        participantId: this.getOrGenerateUserId(),
        participantName: this.userName,
      });
      this.selectedFiles = uploadResult.selectedFiles;
      return uploadResult.uploadedIds;
    } finally {
      this.isUploadingFiles = false;
    }
  }

  private async sendMessage(message: string): Promise<void> {
    if (!this.sessionId || !message.trim()) return;

    try {
      let attachmentIds: number[] = [];
      if (this.allowAttachments && this.selectedFiles.length > 0) {
        attachmentIds = await this.uploadFiles();

        // Check if any files have errors after upload attempt
        const hasErrors = this.selectedFiles.some(sf => sf.error);
        if (hasErrors) {
          // Don't send the message, let user fix file issues first
          this.handleError('Please remove or fix file errors before sending your message.');
          return;
        }
      }

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

      // Add user message immediately with attachments info
      const userMessage: ChatMessage = {
        created_at: new Date().toISOString(),
        role: 'user',
        content: message.trim(),
        attachments: this.allowAttachments ? this.selectedFiles
          .filter(sf => !sf.error && sf.uploaded)
          .map(sf => ({
            name: sf.file.name,
            content_type: sf.file.type,
            size: sf.file.size,
          })) : []
      };
      this.messages = [...this.messages, userMessage];
      this.saveSessionToStorage();
      this.messageInput = '';
      if (this.allowAttachments) {
        this.selectedFiles = []; // Clear selected files after sending
      }
      this.scrollToBottom();

      const requestBody: any = { message: message.trim() };
      if (this.allowAttachments && attachmentIds.length > 0) {
        requestBody.attachment_ids = attachmentIds;
      }

      const data = await this.getChatService().sendMessage(this.sessionId, requestBody);

      if (data.status === 'error') {
        throw new Error(data.error || 'Failed to send message');
      }

      this.startTaskPolling(data.task_id);
    } catch (error) {
      const errorText = error instanceof Error ? error.message : 'Failed to send message';
      this.handleError(errorText);
    }
  }

  private handleStarterQuestionClick(question: string): void {
    this.sendMessage(question);
  }

  /**
   * Scroll the message container to the bottom.
   * @param forceEnd When `false`, scroll the top of the last message into view.
   *    When `true`, scroll all the way to the end of the last message.
   */
  private scrollToBottom(forceEnd: boolean =false): void {
    setTimeout(() => {
      if (this.messageListRef) {
        const lastChild = this.messageListRef.lastElementChild;
        if (!forceEnd && lastChild) {
          // scroll so that the top of the last message is in the centre of the message container
          const parentRect = this.messageListRef.getBoundingClientRect();
          const childRect = lastChild.getBoundingClientRect();
          const currentScrollTop = this.messageListRef.scrollTop;
          const childTopRelativeToParent = childRect.top - parentRect.top;
          const targetScroll = currentScrollTop + childTopRelativeToParent - (parentRect.height / 2);
          this.messageListRef.scrollTo({
              top: targetScroll,
              behavior: 'smooth'
          });
        } else {
          this.messageListRef.scrollTop = this.messageListRef.scrollHeight;
        }
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
  }

  private handleFileSelect(event: Event): void {
    if (!this.allowAttachments) return;

    const input = event.target as HTMLInputElement;
    if (!input.files || input.files.length === 0) return;

    this.selectedFiles = this.attachmentManager.addFiles(this.selectedFiles, input.files);
    input.value = '';
  }

  private removeSelectedFile(index: number): void {
    if (!this.allowAttachments) return;
    this.selectedFiles = this.attachmentManager.removeFile(this.selectedFiles, index);
  }

  private formatFileSize(bytes: number): string {
    if (bytes === 0) return '0 KB';
    const k = 1024;

    if (bytes < k * k) {
      // Less than 1MB, show in KB
      return Math.round(bytes / k * 100) / 100 + ' KB';
    } else {
      return Math.round(bytes / (k * k) * 100) / 100 + ' MB';
    }
  }

  private formatTime(dateString: string): string {
    const date = new Date(dateString);
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  private toggleWindowVisibility() {
    this.visible = !this.visible;
  }

  /**
   * Watch for changes to the `visible` attribute and update accordingly.
   *
   * @param visible - The new value for the field.
   */
  @Watch('visible')
  async visibilityHandler(visible: boolean) {
    if (visible) {
      this.initializePosition();
    }
    if (visible && !this.sessionId) {
      await this.startSession();
    } else if (!visible) {
      this.stopMessagePolling();
    } else {
      this.scrollToBottom(true);
      this.startMessagePolling();
    }
  }

  private startTaskPolling(taskId: string): void {
    if (!this.sessionId) return;

    this.currentPollTaskId = taskId;
    this.isTyping = true;
    this.stopMessagePolling();

    if (this.taskPollingHandle) {
      this.taskPollingHandle.cancel();
    }

    this.taskPollingHandle = this.getChatService().pollTask(this.sessionId, taskId, {
      onMessage: (message) => {
        this.messages = [...this.messages, message];
        this.saveSessionToStorage();
        this.scrollToBottom();
        this.isTyping = false;
        this.currentPollTaskId = '';
        this.taskPollingHandle = undefined;
        this.startMessagePolling();
        this.focusInput();
      },
      onTimeout: () => {
        const timeoutMessage: ChatMessage = {
          created_at: new Date().toISOString(),
          role: 'system',
          content: 'The response is taking longer than expected. The system may be experiencing delays. Please try sending your message again.',
          attachments: []
        };
        this.messages = [...this.messages, timeoutMessage];
        this.saveSessionToStorage();
        this.scrollToBottom();
        this.isTyping = false;
        this.currentPollTaskId = '';
        this.taskPollingHandle = undefined;
        this.startMessagePolling();
        this.focusInput();
      },
      onError: (error) => {
        this.handleError(error.message);
        this.taskPollingHandle = undefined;
        this.startMessagePolling();
      }
    });
  }

  private startMessagePolling(): void {
    if (!this.sessionId || this.currentPollTaskId || !this.visible) {
      return;
    }

    if (this.messagePollingHandle) {
      return;
    }

    this.messagePollingHandle = this.getChatService().startMessagePolling(this.sessionId, {
      getSince: () => this.messages.length > 0 ? this.messages.at(-1)?.created_at : undefined,
      onMessages: (messages) => {
        if (messages.length === 0) return;
        this.messages = [...this.messages, ...messages];
        this.saveSessionToStorage();
        this.scrollToBottom();
        this.focusInput();
      },
      onError: () => {
        // Silently ignore polling errors to match previous behaviour
      }
    });
  }

  private stopMessagePolling(): void {
    if (this.messagePollingHandle) {
      this.messagePollingHandle.stop();
      this.messagePollingHandle = undefined;
    } else {
      this.chatService?.stopMessagePolling();
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
    if (this.positionInitialized) {
      return;
    }
    this.positionInitialized = true;

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
    this.positionInitialized = false;
    this.initializePosition();
  };

  private getDefaultIconUrl(): string {
    return `${this.apiBaseUrl}/static/images/favicons/favicon.svg`;
  }

  private getWelcomeMessages(): string[] {
    const translated = this.translationManager.getArray("welcomeMessages");
    return translated && translated.length > 0
      ? translated
      : this.parsedWelcomeMessages;
  }

  private getStarterQuestions(): string[] {
    const translated = this.translationManager.getArray("starterQuestions");
    return translated && translated.length > 0
      ? translated
      : this.parsedStarterQuestions;
  }

  private getButtonClasses(): string {
    const translatedButtonText = this.translationManager.get('buttonText');
    const hasText = (translatedButtonText && translatedButtonText.trim()) || (this.buttonText && this.buttonText.trim());
    const baseClass = hasText ? 'chat-btn-text' : 'chat-btn-icon';
    const shapeClass = this.buttonShape === 'round' ? 'round' : '';
    return `${baseClass} ${shapeClass}`.trim();
  }

  private renderButton() {
    const translatedButtonText = this.translationManager.get('buttonText');
    const hasText = (translatedButtonText && translatedButtonText.trim()) || (this.buttonText && this.buttonText.trim());
    const hasCustomIcon = this.iconUrl && this.iconUrl.trim();
    const iconSrc = hasCustomIcon ? this.iconUrl : this.getDefaultIconUrl();
    const buttonClasses = this.getButtonClasses();
    const finalButtonText = translatedButtonText || this.buttonText;
    if (hasText) {
      return (
        <button
          class={buttonClasses}
          onClick={() => this.toggleWindowVisibility()}
          aria-label={`Open chat - ${finalButtonText}`}
          title={finalButtonText}
        >
          <img src={iconSrc} alt="" />
          <span>{finalButtonText}</span>
        </button>
      );
    } else {
      return (
        <button
          class={buttonClasses}
          onClick={() => this.toggleWindowVisibility()}
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

  private showConfirmationDialog(): void {
    this.showNewChatConfirmation = true;
  }

  private hideConfirmationDialog(): void {
    this.showNewChatConfirmation = false;
  }

  private async confirmNewChat(): Promise<void> {
    this.hideConfirmationDialog();
    await this.actuallyStartNewChat();
  }

  private async actuallyStartNewChat(): Promise<void> {
    this.clearSessionStorage();
    this.sessionId = undefined;
    this.messages = [];
    this.isTyping = false;
    this.currentPollTaskId = '';
    if (this.allowAttachments) {
      this.selectedFiles = [];
    }
    this.cleanup();

    await this.startSession();
  }

  private toggleFullscreen(): void {
    this.isFullscreen = !this.isFullscreen;
    // Reset fullscreen position when toggling
    this.fullscreenPosition = { x: 0 };
  }

  render() {
    // Only show error state for critical errors that prevent the widget from functioning
    if (this.error && !this.sessionId) {
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
              <div class="header-text">{this.translationManager.get('headerText') || this.headerText}</div>
              <div class="header-buttons">
                {/* New Chat button */}
                {this.messages.length > 0 && (
                  <button
                    class="header-button"
                    onClick={() => this.showConfirmationDialog()}
                    title={this.translationManager.get('startNewChat')}
                    aria-label={this.translationManager.get('startNewChat')}
                  >
                    <PlusWithCircleIcon/>
                  </button>
                )}
                {/* Fullscreen toggle button */}
                {this.allowFullScreen && <button
                  class="header-button fullscreen-button"
                  onClick={() => this.toggleFullscreen()}
                  title={this.isFullscreen ? this.translationManager.get('exitFullscreen') : this.translationManager.get('enterFullscreen')}
                  aria-label={this.isFullscreen ? this.translationManager.get('exitFullscreen') : this.translationManager.get('enterFullscreen')}
                >
                  {this.isFullscreen ? <ArrowsPointingInIcon/> : <ArrowsPointingOutIcon/>}
                </button>}

                <button
                  class="header-button"
                  onClick={() => this.visible = false}
                  aria-label={this.translationManager.get('close')}
                >
                  <XMarkIcon/>
                </button>
              </div>
            </div>

            {this.showNewChatConfirmation && (
              <div class="confirmation-overlay">
                <div class="confirmation-dialog">
                  <div class="confirmation-content">
                    <h3 class="confirmation-title">{this.translationManager.get('startNewChatTitle')}</h3>
                    <p class="confirmation-message">
                      {this.translationManager.get('startNewChatMessage') || this.newChatConfirmationMessage}
                    </p>
                    <div class="confirmation-buttons">
                      <button
                        class="confirmation-button confirmation-button-cancel"
                        onClick={() => this.hideConfirmationDialog()}
                      >
                        {this.translationManager.get('cancel')}
                      </button>
                      <button
                        class="confirmation-button confirmation-button-confirm"
                        onClick={() => this.confirmNewChat()}
                      >
                        {this.translationManager.get('confirm')}
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            )}

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
              {(
                <div
                  ref={(el) => this.messageListRef = el}
                  class="messages-container"
                >
                  {this.messages.length === 0 && this.parsedWelcomeMessages.length > 0 && (
                    <div class="welcome-messages">
                      {this.getWelcomeMessages().map((message, index) => (
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
                              <div key={attachmentIndex} class="flex items-center gap-[0.5em]">
                                <span class="message-attachment-icon">
                                  <PaperClipIcon />
                                </span>
                                <span class="message-attachment-name">{attachment.name}</span>
                              </div>
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
                        <span>{this.translationManager.get('typingIndicatorText')}</span>
                        <span class="typing-dots loading"></span>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Starter Questions */}
              {this.messages.length === 0 && this.parsedStarterQuestions.length > 0 && (
                <div class="starter-questions">
                  {this.getStarterQuestions().map((question, index) => (
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

              {/* Selected Files Display */}
              {this.allowAttachments && this.selectedFiles.length > 0 && (
                <div class="selected-files-container">
                  <div class="space-y-[0.25em]">
                    {this.selectedFiles.map((selectedFile, index) => (
                      <div key={index} class="selected-file-item">
                        <div class="flex items-center gap-[0.5em]">
                          <span class="selected-file-icon">
                            <PaperClipIcon/>
                          </span>
                          <span>{selectedFile.file.name}</span>
                          <span class="selected-file-size">({this.formatFileSize(selectedFile.file.size)})</span>
                          {selectedFile.error && (
                            <span class="selected-file-error">{selectedFile.error}</span>
                          )}
                          {selectedFile.uploaded && (
                            <span class="selected-file-success-icon"><CheckDocumentIcon /></span>
                          )}
                        </div>
                        <button
                          onClick={() => this.removeSelectedFile(index)}
                          class="selected-file-remove-button"
                          aria-label={this.translationManager.get('removeFile')}
                        ><XIcon />
                        </button>
                      </div>
                    ))}
                  </div>
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
                      placeholder={this.translationManager.get('typeMessage')}
                      value={this.messageInput}
                      onInput={(e) => this.handleInputChange(e)}
                      onKeyPress={(e) => this.handleKeyPress(e)}
                      disabled={this.isTyping || this.isUploadingFiles}
                    ></textarea>
                    {/* File Upload Button */}
                    {this.allowAttachments && (
                      <input
                        ref={(el) => {
                            // Unclear why but after removing all attachments this is being set to `null`.
                            if (el) {this.fileInputRef = el}
                          }
                        }
                        id="ocs-file-input"
                        type="file"
                        multiple
                        accept={OcsChat.SUPPORTED_FILE_EXTENSIONS.join(',')}
                        onChange={(e) => this.handleFileSelect(e)}
                        class="hidden"
                      />
                    )}
                    {this.allowAttachments && (
                      <button
                        class="file-attachment-button"
                        onClick={() => this.fileInputRef?.click()}
                        disabled={this.isTyping || this.isUploadingFiles}
                        title={this.translationManager.get('attachFiles')}
                        aria-label={this.translationManager.get('attachFiles')}
                      >
                        <PaperClipIcon />
                      </button>
                    )}
                    <button
                      class={`send-button ${
                        !this.isTyping && !!this.messageInput.trim()
                          ? 'send-button-enabled'
                          : 'send-button-disabled'
                      }`}
                      onClick={() => this.sendMessage(this.messageInput)}
                      disabled={this.isTyping || this.isUploadingFiles || !this.messageInput.trim()}
                    >
                      {this.isUploadingFiles ? `${this.translationManager.get('uploading')}...` : this.translationManager.get('sendMessage')}
                    </button>
                  </div>
                </div>
              )}
              <div class="flex items-center justify-center text-[0.8em] font-light w-full text-slate-500 py-[2px]">
                <p>{this.translationManager.get('poweredBy')}{' '} <a class="underline" href="https://www.dimagi.com" target="_blank">Dimagi</a></p>
              </div>
            </div>
          </div>
        )}
      </Host>
    );
  }
}
