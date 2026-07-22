// eslint-disable-next-line @typescript-eslint/no-unused-vars
import { Component, Host, h, Prop, State, Element, Watch, Env } from '@stencil/core';
import {
  XMarkIcon,
  GripDotsVerticalIcon,
  PlusWithCircleIcon,
  ArrowsPointingOutIcon,
  ArrowsPointingInIcon,
  PaperClipIcon,
  CheckDocumentIcon,
  XIcon,
  OcsWidgetAvatar,
} from './icons';
import { renderMarkdownSync as renderMarkdownComplete } from '../../utils/markdown';
import { varToPixels } from '../../utils/utils';
import { TranslationStrings, TranslationManager, defaultTranslations } from '../../utils/translations';
import { ChatSessionService, ChatMessage, MessagePollingHandle, TaskPollingHandle, SessionAccessError } from '../../services/chat-session-service';
import { FileAttachmentManager, SelectedFile } from '../../services/file-attachment-manager';

interface PointerEvent {
  clientX: number;
  clientY: number;
}

interface SessionStorageData {
  sessionId?: string;
  messages: ChatMessage[];
  sessionToken?: string;
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
  private static readonly SUPPORTED_FILE_EXTENSIONS = [
    '.txt',
    '.pdf',
    '.doc',
    '.docx',
    '.xls',
    '.xlsx',
    '.csv',
    '.jpg',
    '.jpeg',
    '.png',
    '.gif',
    '.bmp',
    '.webp',
    '.svg',
    '.mp4',
    '.mov',
    '.avi',
    '.mp3',
    '.wav',
    '.html',
    '.htm',
    '.css',
    '.js',
    '.xml',
    '.md',
    '.ics',
    '.vcf',
    '.rtf',
    '.tsv',
    '.yaml',
    '.yml',
    '.py',
    '.c',
  ];

  /**
   * The ID of the chatbot to connect to.
   */
  @Prop() chatbotId!: string;

  /**
   * The base URL for the API.
   */
  @Prop() apiBaseUrl?: string = 'https://www.openchatstudio.com';

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
   * Whether to show the launcher button. Set to false to hide the button
   * and open the chat window programmatically via the `visible` property.
   */
  @Prop() showButton: boolean = true;

  /**
   * The operating mode of the widget.
   * - 'standard': Default floating window with launcher button.
   * - 'kiosk': Fills parent container, always visible, no header or launcher button.
   *   The parent element must establish a containing block (e.g. `position: relative`).
   */
  @Prop() mode: 'standard' | 'kiosk' = 'standard';

  /**
   * The text to place in the header.
   */
  @Prop() headerText: '';

  /**
   * The message to display in the new chat confirmation dialog.
   */
  @Prop() newChatConfirmationMessage?: string;

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
   * Ignored when `sessionId` is provided.
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
   * Render the widget in a read-only state. When `true`, the chat history stays
   * visible and scrollable and the message input and send controls remain
   * visible but disabled, and message sending is blocked at the source. Useful
   * for maintenance windows, expired sessions, scheduled downtime or
   * post-session review.
   */
  @Prop() disabled: boolean = false;

  /**
   * Optional banner message shown whenever it is set. The banner is always
   * visible (not dismissable) and does not block normal widget usage.
   */
  @Prop() bannerMessage?: string;

  /**
   * Visual style of the banner. One of `default`, `info`, `warning` or `error`.
   */
  @Prop() bannerStyle: 'default' | 'info' | 'warning' | 'error' = 'default';

  /**
   * Where the banner is positioned relative to the chat area: `top` or `bottom`.
   */
  @Prop() bannerPosition: 'top' | 'bottom' = 'top';

  /**
   * The text to display while the assistant is typing/preparing a response.
   */
  @Prop() typingIndicatorText?: string;

  /**
   * The language code for the widget UI (e.g., 'en', 'es', 'fr'). Defaults to en
   */
  @Prop() language?: string;

  @Prop() translationsUrl?: string;

  /**
   * Optional context object to send with each message. This provides page-specific context to the bot.
   */
  @Prop({ mutable: true }) pageContext?: Record<string, any>;

  /**
   * @internal
   * Optional version number of the chatbot to use. Requires authentication.
   * This is for internal use only and is not intended for public-facing widgets.
   */
  @Prop() versionNumber?: number;

  /**
   * The ID of an existing chat session to connect to. When provided, the widget
   * is bound to that session: local session persistence is disabled and the
   * message history is loaded from the server. Intended for host pages that
   * create the session server-side (e.g. the OCS web chat page).
   */
  @Prop() sessionId?: string;

  /**
   * A session token proving access to the session named by `session-id`. Host
   * pages that create the session server-side pass a server-minted token here so
   * the widget can authenticate its requests. Only meaningful with `session-id`.
   */
  @Prop() sessionToken?: string;

  @State() error: string = '';
  @State() messages: ChatMessage[] = [];
  @State() activeSessionId?: string;
  @State() isLoading: boolean = false;
  @State() isTyping: boolean = false;
  @State() typingProgressMessage: string = '';
  @State() messageInput: string = '';
  @State() currentPollTaskId: string = '';
  @State() isDragging: boolean = false;
  @State() dragOffset: { x: number; y: number } = { x: 0, y: 0 };
  @State() windowPosition: { x: number; y: number } = { x: 0, y: 0 };
  @State() fullscreenPosition: { x: number } = { x: 0 };
  @State() parsedWelcomeMessages: string[] = [];
  @State() parsedStarterQuestions: string[] = [];
  @State() generatedUserId?: string;
  @State() isFullscreen: boolean = false;
  @State() showNewChatConfirmation: boolean = false;
  @State() sessionEnded: boolean = false;

  @State() selectedFiles: SelectedFile[] = [];
  @State() isUploadingFiles: boolean = false;
  private buttonPosition: { x: number; y: number } = { x: 30, y: 30 };
  private buttonHorizontalSide: 'left' | 'right' = 'right';
  private buttonVerticalSide: 'top' | 'bottom' = 'bottom';
  @State() isButtonDragging: boolean = false;
  @State() buttonWasDragged: boolean = false;

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
  private buttonRef?: HTMLButtonElement;
  private buttonDragOffset: { x: number; y: number } = { x: 0, y: 0 };
  private rafId: number | null = null;
  private buttonListenersAttached: boolean = false;
  private chatWindowHeight: number = 600;
  private chatWindowWidth: number = 450;
  private chatWindowFullscreenWidth: number = 1024;
  private positionInitialized: boolean = false;
  private internalPageContext?: Record<string, any>;
  private sessionEpoch: number = 0;
  private currentSessionToken?: string;
  @Element() host: HTMLElement;

  async componentWillLoad() {
    if (!this.chatbotId) {
      this.error = 'Chatbot ID is required';
      return;
    }

    if (this.isKioskMode()) {
      this.visible = true;
    }

    await this.initializeTranslations();

    if (this.isSessionBound()) {
      // Bound to an externally-managed session: the host page is the source of truth.
      this.activeSessionId = this.sessionId;
      this.applySessionToken(this.sessionToken);
    } else if (this.persistentSession && this.isLocalStorageAvailable()) {
      // Always try to load existing session if localStorage is available
      const { sessionId, messages, sessionToken } = this.loadSessionFromStorage();
      if (sessionId && messages) {
        this.activeSessionId = sessionId;
        this.messages = messages;
        this.applySessionToken(sessionToken);
      }
    }
    this.parseWelcomeMessages();
    this.parseStarterQuestions();
    this.loadInternalPageContext();
  }

  componentDidLoad() {
    const computedStyle = getComputedStyle(this.host);
    const windowHeightVar = computedStyle.getPropertyValue('--chat-window-height');
    const windowWidthVar = computedStyle.getPropertyValue('--chat-window-width');
    const fullscreenWidthVar = computedStyle.getPropertyValue('--chat-window-fullscreen-width');
    this.chatWindowHeight = varToPixels(windowHeightVar, window.innerHeight, this.chatWindowHeight);
    this.chatWindowWidth = varToPixels(windowWidthVar, window.innerWidth, this.chatWindowWidth);
    this.chatWindowFullscreenWidth = varToPixels(fullscreenWidthVar, window.innerWidth, this.chatWindowFullscreenWidth);
    // Initialize button position from computed styles
    if (this.showButton && !this.isKioskMode()) {
      this.initializeButtonPosition();
    }

    // Defer state changes to avoid triggering them during componentDidLoad
    setTimeout(() => {
      // Restore visible state after dimensions are read so initializePosition
      // uses the correct CSS-derived chatWindowWidth/chatWindowHeight.
      if (!this.isKioskMode() && this.showButton && this.persistentSession && this.isLocalStorageAvailable()) {
        this.restoreVisibleState();
      }

      if (this.visible) {
        if (!this.isKioskMode()) {
          this.initializePosition();
        }
      }

      // Resume polling for existing session (don't auto-start new sessions)
      if (this.visible && this.activeSessionId) {
        if (this.isSessionBound()) {
          void this.loadBoundSessionHistory();
        } else {
          this.startMessagePolling();
        }
      }
    }, 0);

    window.addEventListener('resize', this.handleWindowResize);
  }

  disconnectedCallback() {
    this.cleanup();
    this.removeEventListeners();
    this.removeButtonEventListeners();
    window.removeEventListener('resize', this.handleWindowResize);
  }

  // ---------------------------------------------------------------------------
  // Public event API
  // ---------------------------------------------------------------------------

  /**
   * Dispatch a composed, bubbling CustomEvent on the host element so that
   * embedders can listen with plain addEventListener outside the shadow root.
   *
   * All events are dispatched synchronously so that handlers can update
   * reactive props (e.g. `pageContext`) before the calling code reads them.
   */
  private dispatchWidgetEvent<T extends Record<string, unknown> | null>(name: string, detail: T = null as unknown as T): void {
    this.host.dispatchEvent(
      new CustomEvent<T>(name, {
        bubbles: true,
        composed: true,
        detail,
      }),
    );
  }

  private applySessionToken(token?: string): void {
    this.currentSessionToken = token;
    this.chatService?.setSessionToken(token);
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
        sessionToken: this.currentSessionToken,
      });
    }
    return this.chatService;
  }

  private addErrorMessage(errorText: string): void {
    const errorMessage: ChatMessage = {
      created_at: new Date().toISOString(),
      role: 'system',
      content: `**Error:** ${errorText}\nPlease try again.`,
      attachments: [],
    };

    this.messages = [...this.messages, errorMessage];
    this.saveSessionToStorage();
    this.scrollToBottom();
  }

  /**
   * Recover from a rejected session token (403). Unbound widgets discard the
   * dead session/token, show a notice, and start fresh on the next send; bound
   * widgets cannot restart a host-owned session, so they surface an error.
   */
  private handleSessionAccessError(): void {
    this.cleanup();
    this.isLoading = false;
    this.isTyping = false;
    this.isUploadingFiles = false;
    this.typingProgressMessage = '';

    if (this.isSessionBound()) {
      this.addErrorMessage(this.translationManager.get('status.sessionError', 'This chat session is no longer available.'));
      return;
    }

    this.sessionEpoch += 1;
    this.activeSessionId = undefined;
    this.applySessionToken(undefined);
    this.clearSessionStorage();
    this.addErrorMessage(this.translationManager.get('status.sessionExpired', 'Your chat session expired. Starting a new chat — please resend your message.'));
  }

  /**
   * The server reported the session has ended (e.g. closed from another tab or
   * by the bot). Polling has already stopped; disable the composer and tell the
   * user. Unbound widgets can recover via the "new chat" button (clearSession).
   */
  private handleSessionEnded(): void {
    if (this.sessionEnded) {
      return;
    }
    this.sessionEnded = true;
    this.dispatchWidgetEvent('ocs:session:ended', { sessionId: this.activeSessionId });
    this.stopMessagePolling();
    this.isTyping = false;
    this.typingProgressMessage = '';
    const content = this.translationManager.get('status.chatEnded') ?? 'This chat has ended.';
    // An unbound widget restores persisted messages and re-polls after a reload,
    // so the notice may already be the last message.
    const last = this.messages.at(-1);
    if (last?.role === 'system' && last.content === content) {
      return;
    }
    const notice: ChatMessage = {
      created_at: new Date().toISOString(),
      role: 'system',
      content,
      attachments: [],
    };
    this.messages = [...this.messages, notice];
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

  private loadInternalPageContext() {
    if (this.pageContext === undefined || this.pageContext === null) {
      return;
    }

    if (typeof this.pageContext !== 'object' || Array.isArray(this.pageContext)) {
      console.error('pageContext is expected to be a plain JavaScript object.');
      return;
    }

    this.internalPageContext = this.pageContext;
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
      return defaultTranslations;
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
    const epoch = this.sessionEpoch;
    try {
      this.isLoading = true;

      const userId = this.getOrGenerateUserId();

      const requestBody: Record<string, unknown> = {
        chatbot_id: this.chatbotId,
        session_data: {
          source: 'widget',
          page_url: window.location.href,
        },
        participant_remote_id: userId,
      };

      if (this.userName) {
        requestBody.participant_name = this.userName;
      }

      if (this.versionNumber != null) {
        requestBody.version_number = this.versionNumber;
      }

      const data = await this.getChatService().startSession(requestBody);
      if (epoch !== this.sessionEpoch) return;
      this.activeSessionId = data.session_id;
      this.applySessionToken(data.session_token ?? undefined);
      this.saveSessionToStorage();
      this.dispatchWidgetEvent('ocs:session:started', { sessionId: this.activeSessionId });

      this.startMessagePolling();
    } catch (_error) {
      if (epoch !== this.sessionEpoch) return;
      this.handleError('Failed to start chat session');
    } finally {
      this.isLoading = false;
    }
  }

  /**
   * Load the full message history for a session provided via the `session-id`
   * prop, then begin regular polling.
   */
  private async loadBoundSessionHistory(): Promise<void> {
    const epoch = this.sessionEpoch;
    try {
      const history = await this.getChatService().fetchAllMessages(this.activeSessionId);
      if (epoch !== this.sessionEpoch) return;
      // Keep messages added while the history was loading (e.g. an optimistic
      // user message) by appending any that aren't part of the fetched history.
      const known = new Set(history.map(m => `${m.created_at}|${m.role}|${m.content}`));
      const pending = this.messages.filter(m => !known.has(`${m.created_at}|${m.role}|${m.content}`));
      this.messages = [...history, ...pending];
      this.scrollToBottom(true);
    } catch (error) {
      if (epoch !== this.sessionEpoch) return;
      if (error instanceof SessionAccessError) {
        this.handleSessionAccessError();
        return;
      }
      console.warn('Failed to load chat history:', error);
    }
    this.startMessagePolling();
  }

  private async uploadFiles(): Promise<number[]> {
    if (this.selectedFiles.length === 0 || !this.activeSessionId || !this.allowAttachments) {
      return [];
    }

    this.isUploadingFiles = true;
    try {
      const uploadResult = await this.attachmentManager.uploadPendingFiles(this.selectedFiles, {
        apiBaseUrl: this.apiBaseUrl || 'https://www.openchatstudio.com',
        sessionId: this.activeSessionId,
        participantId: this.getOrGenerateUserId(),
        participantName: this.userName,
        headers: this.getChatService().getUploadHeaders(),
      });
      this.selectedFiles = uploadResult.selectedFiles;
      if (uploadResult.tokenRejected) {
        throw new SessionAccessError(403, 'session_token_required', uploadResult.errorMessage || 'Session token rejected');
      }
      return uploadResult.uploadedIds;
    } finally {
      this.isUploadingFiles = false;
    }
  }

  private async sendMessage(message: string): Promise<void> {
    // Block sending when the widget is disabled so direct calls cannot bypass
    // the hidden composer.
    if (this.disabled) return;
    if (!message.trim() || this.sessionEnded) return;
    const epoch = this.sessionEpoch;

    // Start session if we don't have one yet
    if (!this.activeSessionId) {
      // Prevent concurrent session initialization
      if (this.isLoading) {
        return;
      }
      await this.startSession();
      // Check if session started successfully
      if (!this.activeSessionId) {
        return; // startSession already handled the error
      }
    }

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
      const welcomeMessagesToAdd = this.getWelcomeMessages();
      if (this.messages.length === 0 && welcomeMessagesToAdd.length > 0) {
        const now = new Date();
        const welcomeMessages: ChatMessage[] = welcomeMessagesToAdd.map((welcomeMsg, index) => ({
          created_at: new Date(now.getTime() - (welcomeMessagesToAdd.length - index) * 1000).toISOString(),
          role: 'assistant' as const,
          content: welcomeMsg,
          attachments: [],
        }));
        this.messages = [...this.messages, ...welcomeMessages];
      }

      // Add user message immediately with attachments info
      const userMessage: ChatMessage = {
        created_at: new Date().toISOString(),
        role: 'user',
        content: message.trim(),
        attachments: this.allowAttachments
          ? this.selectedFiles
              .filter(sf => !sf.error && sf.uploaded)
              .map(sf => ({
                name: sf.file.name,
                content_type: sf.file.type,
                size: sf.file.size,
              }))
          : [],
      };
      this.messages = [...this.messages, userMessage];
      this.saveSessionToStorage();
      this.messageInput = '';
      if (this.allowAttachments) {
        this.selectedFiles = []; // Clear selected files after sending
      }
      this.scrollToBottom();

      // Fire before-send first so handlers can update pageContext synchronously
      // before we read internalPageContext into the request body.
      this.dispatchWidgetEvent('ocs:message:before-send', {
        message: message.trim(),
        sessionId: this.activeSessionId ?? '',
      });

      const requestBody: any = { message: message.trim() };
      if (this.allowAttachments && attachmentIds.length > 0) {
        requestBody.attachment_ids = attachmentIds;
      }
      if (this.internalPageContext) {
        requestBody.context = this.internalPageContext;
      }
      if (this.versionNumber != null) {
        requestBody.version_number = this.versionNumber;
      }

      const data = await this.getChatService().sendMessage(this.activeSessionId, requestBody);
      if (epoch !== this.sessionEpoch) return;

      if (data.status === 'error') {
        throw new Error(data.error || 'Failed to send message');
      }

      this.internalPageContext = undefined;
      this.dispatchWidgetEvent('ocs:message:sent', {
        message: message.trim(),
        sessionId: this.activeSessionId,
      });
      this.startTaskPolling(data.task_id);
    } catch (error) {
      if (epoch !== this.sessionEpoch) return;
      if (error instanceof SessionAccessError) {
        this.handleSessionAccessError();
        return;
      }
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
  private scrollToBottom(forceEnd: boolean = false): void {
    setTimeout(() => {
      if (this.messageListRef) {
        const lastChild = this.messageListRef.lastElementChild;
        if (!forceEnd && lastChild) {
          // scroll so that the top of the last message is in the centre of the message container
          const parentRect = this.messageListRef.getBoundingClientRect();
          const childRect = lastChild.getBoundingClientRect();
          const currentScrollTop = this.messageListRef.scrollTop;
          const childTopRelativeToParent = childRect.top - parentRect.top;
          const targetScroll = currentScrollTop + childTopRelativeToParent - parentRect.height / 2;
          this.messageListRef.scrollTo({
            top: targetScroll,
            behavior: 'smooth',
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
      return Math.round((bytes / k) * 100) / 100 + ' KB';
    } else {
      return Math.round((bytes / (k * k)) * 100) / 100 + ' MB';
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
   * Watch for changes to the `pageContext` prop and sync to internal variable.
   *
   * @param pageContext - The new value for the field.
   */
  @Watch('pageContext')
  pageContextHandler() {
    this.loadInternalPageContext();
  }

  @Watch('chatbotId')
  @Watch('versionNumber')
  async chatbotConfigHandler() {
    await this.clearSession();
  }

  /**
   * Watch for changes to the `visible` attribute and update accordingly.
   *
   * @param visible - The new value for the field.
   */
  @Watch('visible')
  async visibilityHandler(visible: boolean) {
    // Kiosk mode is always visible
    if (this.isKioskMode() && !visible) {
      this.visible = true;
      return;
    }

    this.saveVisibleState(visible);
    this.dispatchWidgetEvent(visible ? 'ocs:open' : 'ocs:close');

    if (this.isButtonDragging) {
      this.isButtonDragging = false;
      this.buttonWasDragged = false;
      this.removeButtonEventListeners();
    }

    if (visible) {
      if (!this.isKioskMode()) {
        this.initializePosition();
      }

      // Resume polling for existing session (don't auto-start new sessions)
      if (this.activeSessionId) {
        this.scrollToBottom(true);
        if (this.isSessionBound() && this.messages.length === 0) {
          // A bound widget that was hidden at load has not fetched its history yet.
          void this.loadBoundSessionHistory();
        } else {
          this.startMessagePolling();
        }
      }
    } else {
      this.stopMessagePolling();
    }
  }

  private startTaskPolling(taskId: string): void {
    if (!this.activeSessionId) return;

    this.currentPollTaskId = taskId;
    this.isTyping = true;
    this.stopMessagePolling();

    if (this.taskPollingHandle) {
      this.taskPollingHandle.cancel();
    }

    this.taskPollingHandle = this.getChatService().pollTask(this.activeSessionId, taskId, {
      onMessage: message => {
        this.messages = [...this.messages, message];
        this.saveSessionToStorage();
        this.dispatchWidgetEvent('ocs:message:received', {
          message: { ...message },
          sessionId: this.activeSessionId ?? '',
        });
        this.scrollToBottom();
        this.isTyping = false;
        this.typingProgressMessage = '';
        this.currentPollTaskId = '';
        this.taskPollingHandle = undefined;
        this.startMessagePolling();
        this.focusInput();
      },
      onProgress: message => {
        this.typingProgressMessage = message;
      },
      onTimeout: () => {
        const timeoutMessage: ChatMessage = {
          created_at: new Date().toISOString(),
          role: 'system',
          content: 'The response is taking longer than expected. The system may be experiencing delays. Please try sending your message again.',
          attachments: [],
        };
        this.messages = [...this.messages, timeoutMessage];
        this.saveSessionToStorage();
        this.scrollToBottom();
        this.isTyping = false;
        this.typingProgressMessage = '';
        this.currentPollTaskId = '';
        this.taskPollingHandle = undefined;
        this.startMessagePolling();
        this.focusInput();
      },
      onError: error => {
        this.typingProgressMessage = '';
        this.taskPollingHandle = undefined;
        if (error instanceof SessionAccessError) {
          this.handleSessionAccessError();
          return;
        }
        this.handleError(error.message);
        this.startMessagePolling();
      },
    });
  }

  private startMessagePolling(): void {
    if (!this.activeSessionId || this.currentPollTaskId || !this.visible || this.sessionEnded) {
      return;
    }

    if (this.messagePollingHandle) {
      return;
    }

    this.messagePollingHandle = this.getChatService().startMessagePolling(this.activeSessionId, {
      getSince: () => (this.messages.length > 0 ? this.messages.at(-1)?.created_at : undefined),
      onMessages: messages => {
        if (messages.length === 0) return;
        this.messages = [...this.messages, ...messages];
        this.saveSessionToStorage();
        for (const message of messages) {
          if (message.role !== 'user') {
            this.dispatchWidgetEvent('ocs:message:received', {
              message: { ...message },
              sessionId: this.activeSessionId ?? '',
            });
          }
        }
        this.scrollToBottom();
        this.focusInput();
      },
      onSessionEnded: () => {
        this.messagePollingHandle = undefined;
        this.handleSessionEnded();
      },
      onError: () => {
        // Silently ignore polling errors to match previous behaviour
      },
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
    if (this.isKioskMode()) {
      return 'chat-window-kiosk';
    }
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
    if (this.isKioskMode()) {
      return {};
    }
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
          y: windowHeight - this.chatWindowHeight - OcsChat.WINDOW_MARGIN,
        };
        break;
      case 'right':
        this.windowPosition = {
          x: windowWidth - chatWidth - OcsChat.WINDOW_MARGIN,
          y: windowHeight - this.chatWindowHeight - OcsChat.WINDOW_MARGIN,
        };
        break;
      case 'center':
        this.windowPosition = {
          x: (windowWidth - chatWidth) / 2,
          y: (windowHeight - this.chatWindowHeight) / 2,
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
        y: pointer.clientY,
      };
    } else {
      const rect = this.chatWindowRef.getBoundingClientRect();
      this.dragOffset = {
        x: pointer.clientX - rect.left,
        y: pointer.clientY - rect.top,
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
        x: Math.max(-maxOffset, Math.min(maxOffset, deltaX)),
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
        y: Math.max(0, Math.min(newY, windowHeight - chatHeight)),
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
    if (this.isKioskMode()) return;
    this.positionInitialized = false;
    this.initializePosition();

    // Revalidate button position after resize to keep it within viewport bounds
    if (this.isButtonDraggable()) {
      const windowWidth = window.innerWidth;
      const windowHeight = window.innerHeight;
      const buttonWidth = this.buttonRef?.offsetWidth || 60;
      const buttonHeight = this.buttonRef?.offsetHeight || 60;
      const minPadding = 10;

      this.buttonPosition = {
        x: Math.max(minPadding, Math.min(this.buttonPosition.x, windowWidth - buttonWidth - minPadding)),
        y: Math.max(minPadding, Math.min(this.buttonPosition.y, windowHeight - buttonHeight - minPadding)),
      };

      this.updateHostPosition();
    }
  };

  // Button positioning and drag handlers
  private initializeButtonPosition(): void {
    const computedStyle = getComputedStyle(this.host);
    const position = computedStyle.getPropertyValue('position');

    // Only enable dragging if the host element is positioned fixed
    if (position !== 'fixed') {
      return;
    }

    const rect = this.host.getBoundingClientRect();
    const windowWidth = window.innerWidth;
    const windowHeight = window.innerHeight;

    const left = computedStyle.getPropertyValue('left');
    const right = computedStyle.getPropertyValue('right');
    const top = computedStyle.getPropertyValue('top');
    const bottom = computedStyle.getPropertyValue('bottom');

    const hasLeft = !this.isAutoPosition(left);
    const hasTop = !this.isAutoPosition(top);

    this.buttonHorizontalSide = hasLeft ? 'left' : 'right';
    this.buttonVerticalSide = hasTop ? 'top' : 'bottom';

    const resolvedRight = this.getNumericPositionValue(right, Math.max(0, windowWidth - rect.right));
    const resolvedLeft = this.getNumericPositionValue(left, Math.max(0, rect.left));
    const resolvedBottom = this.getNumericPositionValue(bottom, Math.max(0, windowHeight - rect.bottom));
    const resolvedTop = this.getNumericPositionValue(top, Math.max(0, rect.top));

    const horizontalValue = this.buttonHorizontalSide === 'left' ? resolvedLeft : resolvedRight;
    const verticalValue = this.buttonVerticalSide === 'top' ? resolvedTop : resolvedBottom;

    this.buttonPosition = {
      x: horizontalValue,
      y: verticalValue,
    };

    // Apply the position to the host
    this.updateHostPosition();
  }

  private updateHostPosition(): void {
    this.host.style.position = 'fixed';
    if (this.buttonHorizontalSide === 'left') {
      this.host.style.left = `${this.buttonPosition.x}px`;
      this.host.style.right = 'auto';
    } else {
      this.host.style.right = `${this.buttonPosition.x}px`;
      this.host.style.left = 'auto';
    }

    if (this.buttonVerticalSide === 'top') {
      this.host.style.top = `${this.buttonPosition.y}px`;
      this.host.style.bottom = 'auto';
    } else {
      this.host.style.bottom = `${this.buttonPosition.y}px`;
      this.host.style.top = 'auto';
    }
  }

  private isButtonDraggable(): boolean {
    const computedStyle = getComputedStyle(this.host);
    return computedStyle.getPropertyValue('position') === 'fixed';
  }

  private handleButtonMouseDown = (event: MouseEvent): void => {
    if (!this.buttonRef || !this.isButtonDraggable()) return;

    event.preventDefault();
    event.stopPropagation();

    const pointer = this.getPointerCoordinates(event);
    if (!pointer) return;

    this.isButtonDragging = true;
    this.buttonWasDragged = false; // Reset the drag flag
    const rect = this.host.getBoundingClientRect();
    this.buttonDragOffset = {
      x: pointer.clientX - rect.left,
      y: pointer.clientY - rect.top,
    };

    this.addButtonEventListeners();
  };

  private handleButtonTouchStart = (event: TouchEvent): void => {
    if (!this.buttonRef || !this.isButtonDraggable()) return;

    event.preventDefault();
    event.stopPropagation();

    const pointer = this.getPointerCoordinates(event);
    if (!pointer) return;

    this.isButtonDragging = true;
    this.buttonWasDragged = false; // Reset the drag flag
    const rect = this.host.getBoundingClientRect();
    this.buttonDragOffset = {
      x: pointer.clientX - rect.left,
      y: pointer.clientY - rect.top,
    };

    this.addButtonEventListeners();
  };

  private handleButtonMouseMove = (event: MouseEvent): void => {
    if (!this.isButtonDragging) return;

    const pointer = this.getPointerCoordinates(event);
    if (!pointer) return;

    this.updateButtonPosition(pointer);
  };

  private handleButtonTouchMove = (event: TouchEvent): void => {
    if (!this.isButtonDragging) return;

    event.preventDefault();

    const pointer = this.getPointerCoordinates(event);
    if (!pointer) return;

    this.updateButtonPosition(pointer);
  };

  private updateButtonPosition(pointer: PointerEvent): void {
    const windowWidth = window.innerWidth;
    const windowHeight = window.innerHeight;

    const buttonWidth = this.buttonRef?.offsetWidth || 60;
    const buttonHeight = this.buttonRef?.offsetHeight || 60;
    const minPadding = 10;

    const candidateLeft = pointer.clientX - this.buttonDragOffset.x;
    const candidateTop = pointer.clientY - this.buttonDragOffset.y;

    const minLeft = minPadding;
    const maxLeft = windowWidth - buttonWidth - minPadding;
    const minTop = minPadding;
    const maxTop = windowHeight - buttonHeight - minPadding;

    const constrainedLeft = Math.max(minLeft, Math.min(candidateLeft, maxLeft));
    const constrainedTop = Math.max(minTop, Math.min(candidateTop, maxTop));

    const newHorizontalValue = this.buttonHorizontalSide === 'left' ? constrainedLeft : Math.max(minPadding, windowWidth - (constrainedLeft + buttonWidth));
    const newVerticalValue = this.buttonVerticalSide === 'top' ? constrainedTop : Math.max(minPadding, windowHeight - (constrainedTop + buttonHeight));

    if (newHorizontalValue !== this.buttonPosition.x || newVerticalValue !== this.buttonPosition.y) {
      this.buttonWasDragged = true;
      this.buttonPosition = { x: newHorizontalValue, y: newVerticalValue };

      if (this.rafId === null) {
        this.rafId = requestAnimationFrame(() => {
          this.updateHostPosition();
          this.rafId = null;
        });
      }
    }
  }

  private handleButtonMouseUp = (): void => {
    if (this.isButtonDragging) {
      this.isButtonDragging = false;
      this.removeButtonEventListeners();
    }
  };

  private handleButtonTouchEnd = (): void => {
    if (this.isButtonDragging) {
      this.isButtonDragging = false;
      this.removeButtonEventListeners();
    }
  };

  private handleButtonClick = (): void => {
    // Only toggle visibility if the button wasn't dragged
    if (!this.buttonWasDragged) {
      this.toggleWindowVisibility();
    }
    // Reset the flag after handling the click
    this.buttonWasDragged = false;
  };

  private addButtonEventListeners(): void {
    if (this.buttonListenersAttached) {
      return;
    }

    document.addEventListener('mousemove', this.handleButtonMouseMove);
    document.addEventListener('mouseup', this.handleButtonMouseUp);
    document.addEventListener('touchmove', this.handleButtonTouchMove, { passive: false });
    document.addEventListener('touchend', this.handleButtonTouchEnd);
    this.buttonListenersAttached = true;
  }

  private removeButtonEventListeners(): void {
    if (!this.buttonListenersAttached) {
      return;
    }

    if (this.rafId !== null) {
      cancelAnimationFrame(this.rafId);
      this.rafId = null;
    }

    document.removeEventListener('mousemove', this.handleButtonMouseMove);
    document.removeEventListener('mouseup', this.handleButtonMouseUp);
    document.removeEventListener('touchmove', this.handleButtonTouchMove);
    document.removeEventListener('touchend', this.handleButtonTouchEnd);
    this.buttonListenersAttached = false;
  }

  private isAutoPosition(value: string): boolean {
    const trimmed = value.trim();
    return trimmed === '' || trimmed === 'auto';
  }

  private parsePixelValue(value: string): number | null {
    const trimmed = value.trim();
    if (trimmed === '' || trimmed === 'auto') {
      return null;
    }

    if (trimmed.endsWith('px')) {
      const parsed = parseFloat(trimmed);
      return Number.isFinite(parsed) ? parsed : null;
    }

    const numeric = Number(trimmed);
    if (Number.isFinite(numeric)) {
      return numeric;
    }

    return null;
  }

  private getNumericPositionValue(value: string, fallback: number): number {
    const parsed = this.parsePixelValue(value);
    if (parsed !== null) {
      return parsed;
    }
    return fallback;
  }

  private getWelcomeMessages(): string[] {
    const translated = this.translationManager.getArray('content.welcomeMessages');
    return translated && translated.length > 0 ? translated : this.parsedWelcomeMessages;
  }

  private getStarterQuestions(): string[] {
    const translated = this.translationManager.getArray('content.starterQuestions');
    return translated && translated.length > 0 ? translated : this.parsedStarterQuestions;
  }

  private getButtonClasses(): string {
    const buttonText = this.translationManager.get('branding.buttonText', this.buttonText);
    const hasText = !!(buttonText && buttonText.trim());
    const baseClass = hasText ? 'chat-btn-text' : 'chat-btn-icon';
    const shapeClass = this.buttonShape === 'round' ? 'round' : '';
    return `${baseClass} ${shapeClass}`.trim();
  }

  private renderButton() {
    if (!this.showButton || this.isKioskMode()) {
      return null;
    }
    const buttonText = this.translationManager.get('branding.buttonText', this.buttonText);
    const hasText = !!(buttonText && buttonText.trim());
    const hasCustomIcon = this.iconUrl && this.iconUrl.trim();
    const buttonClasses = this.getButtonClasses();
    const finalButtonText = buttonText ?? '';
    const openLabel = this.translationManager.get('launcher.open') ?? '';
    const buttonAriaLabel = finalButtonText ? `${openLabel} - ${finalButtonText}` : openLabel;

    // Only show drag cursor if button is draggable
    const isDraggable = this.isButtonDraggable();
    const buttonStyle = isDraggable
      ? {
          cursor: this.isButtonDragging ? 'grabbing' : 'grab',
        }
      : {};

    if (hasText) {
      return (
        <button
          ref={el => (this.buttonRef = el)}
          class={buttonClasses}
          aria-label={buttonAriaLabel}
          title={finalButtonText || openLabel}
          style={buttonStyle}
          onClick={() => this.handleButtonClick()}
          onMouseDown={e => this.handleButtonMouseDown(e)}
          onTouchStart={e => this.handleButtonTouchStart(e)}
          aria-grabbed={this.isButtonDragging}
          aria-describedby={isDraggable ? 'chat-button-drag-hint' : undefined}
        >
          {hasCustomIcon ? <img src={this.iconUrl} alt="" /> : <OcsWidgetAvatar />}
          <span>{finalButtonText}</span>
          {isDraggable && (
            <span id="chat-button-drag-hint" style={{ display: 'none' }}>
              Draggable. Use mouse or touch to reposition.
            </span>
          )}
        </button>
      );
    } else {
      return (
        <button
          ref={el => (this.buttonRef = el)}
          class={buttonClasses}
          aria-label={openLabel}
          title={openLabel}
          style={buttonStyle}
          onClick={() => this.handleButtonClick()}
          onMouseDown={e => this.handleButtonMouseDown(e)}
          onTouchStart={e => this.handleButtonTouchStart(e)}
          aria-grabbed={this.isButtonDragging}
          aria-describedby={isDraggable ? 'chat-button-drag-hint' : undefined}
        >
          {hasCustomIcon ? <img src={this.iconUrl} alt="" /> : <OcsWidgetAvatar />}
          {isDraggable && (
            <span id="chat-button-drag-hint" style={{ display: 'none' }}>
              Draggable. Use mouse or touch to reposition.
            </span>
          )}
        </button>
      );
    }
  }

  private getStorageKeys() {
    return {
      sessionId: `ocs-chat-session-${this.chatbotId}`,
      messages: `ocs-chat-messages-${this.chatbotId}`,
      lastActivity: `ocs-chat-activity-${this.chatbotId}`,
      visible: `ocs-chat-visible-${this.chatbotId}`,
      sessionToken: `ocs-chat-token-${this.chatbotId}`,
    };
  }

  private saveSessionToStorage(): void {
    if (!this.persistentSession || this.isSessionBound()) {
      return;
    }
    const keys = this.getStorageKeys();
    try {
      if (this.activeSessionId) {
        localStorage.setItem(keys.sessionId, this.activeSessionId);
        localStorage.setItem(keys.lastActivity, new Date().toISOString());
        if (this.currentSessionToken) {
          localStorage.setItem(keys.sessionToken, this.currentSessionToken);
        } else {
          localStorage.removeItem(keys.sessionToken);
        }
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
            return { messages: [] };
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

      const sessionToken = localStorage.getItem(keys.sessionToken) ?? undefined;

      return { sessionId, messages, sessionToken };
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
    let stored: string | null = null;
    try {
      stored = localStorage.getItem(storageKey);
    } catch {
      // localStorage blocked; fall through to in-memory id generation
    }
    if (stored) {
      this.generatedUserId = stored;
      return stored;
    }

    const array = new Uint8Array(9);
    window.crypto.getRandomValues(array);
    const randomString = Array.from(array, byte => byte.toString(36))
      .join('')
      .substr(0, 9);
    const newUserId = `ocs:${Date.now()}_${randomString}`;
    this.generatedUserId = newUserId;
    try {
      localStorage.setItem(storageKey, newUserId);
    } catch {
      // localStorage blocked; the generated id lives in component state for this page
    }

    return newUserId;
  }

  private saveVisibleState(visible: boolean): void {
    // Kiosk visibility is forced, so persisting it would only leak into a
    // standard-mode widget for the same chatbot on another page.
    if (!this.persistentSession || this.isKioskMode()) return;
    try {
      const keys = this.getStorageKeys();
      localStorage.setItem(keys.visible, visible ? '1' : '0');
    } catch {
      // ignore
    }
  }

  private restoreVisibleState(): void {
    try {
      const keys = this.getStorageKeys();
      const stored = localStorage.getItem(keys.visible);
      if (stored === '1') {
        this.visible = true;
      }
    } catch {
      // ignore
    }
  }

  private clearSessionStorage(): void {
    const keys = this.getStorageKeys();
    try {
      localStorage.removeItem(keys.sessionId);
      localStorage.removeItem(keys.messages);
      localStorage.removeItem(keys.lastActivity);
      localStorage.removeItem(keys.visible);
      localStorage.removeItem(keys.sessionToken);
    } catch (error) {
      console.warn('Failed to clear chat session from localStorage:', error);
    }
  }

  private isKioskMode(): boolean {
    return this.mode === 'kiosk';
  }

  private isSessionBound(): boolean {
    return !!this.sessionId;
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
    await this.clearSession();
  }

  /**
   * This clears out all data related to the previous session. A new session
   * will start when the user sends a message.
   */
  private async clearSession(): Promise<void> {
    this.sessionEpoch += 1;
    this.clearSessionStorage();
    // A session provided by the host page (session-id prop) cannot be cleared;
    // stay bound to it. Unbound widgets start a new session on the next message.
    this.activeSessionId = this.sessionId;
    this.applySessionToken(this.isSessionBound() ? this.sessionToken : undefined);
    this.messages = [];
    this.isTyping = false;
    this.sessionEnded = false;
    this.currentPollTaskId = '';
    if (this.allowAttachments) {
      this.selectedFiles = [];
    }
    this.cleanup();
    if (this.isSessionBound()) {
      // The host-owned session cannot be cleared: reload its history and
      // resume polling so the widget doesn't end up in a dead state.
      void this.loadBoundSessionHistory();
    }
  }

  private toggleFullscreen(): void {
    this.isFullscreen = !this.isFullscreen;
    // Reset fullscreen position when toggling
    this.fullscreenPosition = { x: 0 };
  }

  /**
   * The widget is read-only: chat history is visible and the composer is shown
   * but disabled, and sending is blocked.
   */
  private isReadOnly(): boolean {
    return this.disabled;
  }

  private hasBanner(): boolean {
    return !!(this.bannerMessage && this.bannerMessage.trim());
  }

  private renderBanner(position: 'top' | 'bottom') {
    if (!this.hasBanner() || this.bannerPosition !== position) {
      return null;
    }
    const style = ['default', 'info', 'warning', 'error'].includes(this.bannerStyle) ? this.bannerStyle : 'default';
    const role = style === 'error' || style === 'warning' ? 'alert' : 'status';
    return (
      <div class={`chat-banner chat-banner-${style} chat-banner-${position}`} role={role}>
        {this.bannerMessage}
      </div>
    );
  }

  render() {
    // Only show error state for critical errors that prevent the widget from functioning
    if (this.error && !this.activeSessionId) {
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
          <div ref={el => (this.chatWindowRef = el)} id="ocs-chat-window" class={this.getPositionClasses()} style={this.getPositionStyles()}>
            {/* Header — hidden in kiosk mode */}
            {!this.isKioskMode() && (
              <div
                class={`chat-header ${this.isDragging ? 'chat-header-dragging' : 'chat-header-draggable'}`}
                onMouseDown={this.handleMouseDown}
                onTouchStart={this.handleTouchStart}
              >
                {/* Drag indicator */}
                <div class="drag-indicator">
                  <div class="drag-dots header-button">
                    <GripDotsVerticalIcon />
                  </div>
                </div>
                <div class="header-text">{this.translationManager.get('branding.headerText', this.headerText)}</div>
                <div class="header-buttons">
                  {/* New Chat button — hidden for bound sessions (the host page owns the session lifecycle) */}
                  {this.messages.length > 0 && !this.isSessionBound() && (
                    <button
                      class="header-button"
                      onClick={() => this.showConfirmationDialog()}
                      title={this.translationManager.get('window.newChat')}
                      aria-label={this.translationManager.get('window.newChat')}
                    >
                      <PlusWithCircleIcon />
                    </button>
                  )}
                  {/* Fullscreen toggle button */}
                  {this.allowFullScreen && (
                    <button
                      class="header-button fullscreen-button"
                      onClick={() => this.toggleFullscreen()}
                      title={this.isFullscreen ? this.translationManager.get('window.exitFullscreen') : this.translationManager.get('window.fullscreen')}
                      aria-label={this.isFullscreen ? this.translationManager.get('window.exitFullscreen') : this.translationManager.get('window.fullscreen')}
                    >
                      {this.isFullscreen ? <ArrowsPointingInIcon /> : <ArrowsPointingOutIcon />}
                    </button>
                  )}

                  <button class="header-button" onClick={() => (this.visible = false)} aria-label={this.translationManager.get('window.close')}>
                    <XMarkIcon />
                  </button>
                </div>
              </div>
            )}

            {!this.isKioskMode() && this.showNewChatConfirmation && (
              <div class="confirmation-overlay">
                <div class="confirmation-dialog">
                  <div class="confirmation-content">
                    <h3 class="confirmation-title">{this.translationManager.get('modal.newChatTitle')}</h3>
                    <p class="confirmation-message">{this.translationManager.get('modal.newChatBody', this.newChatConfirmationMessage)}</p>
                    <div class="confirmation-buttons">
                      <button class="confirmation-button confirmation-button-cancel" onClick={() => this.hideConfirmationDialog()}>
                        {this.translationManager.get('modal.cancel')}
                      </button>
                      <button class="confirmation-button confirmation-button-confirm" onClick={() => this.confirmNewChat()}>
                        {this.translationManager.get('modal.confirm')}
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Chat Content */}
            <div class="chat-content">
              {/* Banner (top) */}
              {this.renderBanner('top')}

              {/* Loading State */}
              {this.isLoading && !this.activeSessionId && (
                <div class="loading-container">
                  <div class="loading-spinner"></div>
                  <span class="loading-text">{this.translationManager.get('status.starting')}</span>
                </div>
              )}

              {/* Messages */}
              {
                <div ref={el => (this.messageListRef = el)} class="messages-container">
                  {this.messages.length === 0 && this.getWelcomeMessages().length > 0 && (
                    <div class="welcome-messages">
                      {this.getWelcomeMessages().map((message, index) => (
                        <div key={`welcome-${index}`} class="message-row message-row-assistant">
                          <div class="message-bubble message-bubble-assistant">
                            <div class="chat-markdown" innerHTML={renderMarkdownComplete(message)}></div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                  {/* Regular Chat Messages */}
                  {this.messages.map((message, index) => (
                    <div key={index} class={`message-row ${message.role === 'user' ? 'message-row-user' : 'message-row-assistant'}`}>
                      <div
                        class={`message-bubble ${
                          message.role === 'user' ? 'message-bubble-user' : message.role === 'assistant' ? 'message-bubble-assistant' : 'message-bubble-system'
                        }`}
                      >
                        <div class="chat-markdown" innerHTML={renderMarkdownComplete(message.content)}></div>
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
                        <div class="message-timestamp">{this.formatTime(message.created_at)}</div>
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
                        <span>{this.typingProgressMessage || this.translationManager.get('status.typing', this.typingIndicatorText)}</span>
                        <span class="typing-dots loading"></span>
                      </div>
                    </div>
                  )}
                </div>
              }

              {/* Starter Questions */}
              {!this.isReadOnly() && this.messages.length === 0 && this.getStarterQuestions().length > 0 && (
                <div class="starter-questions">
                  {this.getStarterQuestions().map((question, index) => (
                    <div key={`starter-${index}`} class="starter-question-row">
                      <button class="starter-question" onClick={() => this.handleStarterQuestionClick(question)}>
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
                            <PaperClipIcon />
                          </span>
                          <span>{selectedFile.file.name}</span>
                          <span class="selected-file-size">({this.formatFileSize(selectedFile.file.size)})</span>
                          {selectedFile.error && <span class="selected-file-error">{selectedFile.error}</span>}
                          {selectedFile.uploaded && (
                            <span class="selected-file-success-icon">
                              <CheckDocumentIcon />
                            </span>
                          )}
                        </div>
                        <button onClick={() => this.removeSelectedFile(index)} class="selected-file-remove-button" aria-label={this.translationManager.get('attach.remove')}>
                          <XIcon />
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Banner (bottom) — sits directly above the input area */}
              {this.renderBanner('bottom')}

              {/* Input Area — kept visible but disabled when the widget is read-only */}
              <div class="input-area">
                <div class="input-container">
                  <textarea
                    ref={el => (this.textareaRef = el)}
                    class="message-textarea"
                    rows={1}
                    placeholder={this.sessionEnded ? this.translationManager.get('status.chatEnded') : this.translationManager.get('composer.placeholder')}
                    value={this.messageInput}
                    onInput={e => this.handleInputChange(e)}
                    onKeyPress={e => this.handleKeyPress(e)}
                    disabled={this.isReadOnly() || this.isTyping || this.isUploadingFiles || this.isLoading || this.sessionEnded}
                  ></textarea>
                  {/* File Upload Button */}
                  {this.allowAttachments && (
                    <input
                      ref={el => {
                        // Unclear why but after removing all attachments this is being set to `null`.
                        if (el) {
                          this.fileInputRef = el;
                        }
                      }}
                      id="ocs-file-input"
                      type="file"
                      multiple
                      accept={OcsChat.SUPPORTED_FILE_EXTENSIONS.join(',') + ',text/*'}
                      onChange={e => this.handleFileSelect(e)}
                      class="hidden"
                    />
                  )}
                  {this.allowAttachments && (
                    <button
                      class="file-attachment-button"
                      onClick={() => this.fileInputRef?.click()}
                      disabled={this.isReadOnly() || this.isTyping || this.isUploadingFiles || this.isLoading || this.sessionEnded}
                      title={this.translationManager.get('attach.add')}
                      aria-label={this.translationManager.get('attach.add')}
                    >
                      <PaperClipIcon />
                    </button>
                  )}
                  <button
                    class={`send-button ${!this.isReadOnly() && !this.isTyping && !this.isLoading && !this.sessionEnded && !!this.messageInput.trim() ? 'send-button-enabled' : 'send-button-disabled'}`}
                    onClick={() => this.sendMessage(this.messageInput)}
                    disabled={this.isReadOnly() || this.isTyping || this.isUploadingFiles || this.isLoading || this.sessionEnded || !this.messageInput.trim()}
                  >
                    {this.isUploadingFiles ? `${this.translationManager.get('status.uploading')}...` : this.translationManager.get('composer.send')}
                  </button>
                </div>
              </div>
              <div class="flex items-center justify-center text-[0.8em] font-light w-full text-slate-500 py-[2px]">
                <p>
                  {this.translationManager.get('branding.poweredBy')}{' '}
                  <a class="underline" href="https://www.dimagi.com" target="_blank" rel="noopener noreferrer">
                    Dimagi
                  </a>
                </p>
              </div>
            </div>
          </div>
        )}
      </Host>
    );
  }
}
