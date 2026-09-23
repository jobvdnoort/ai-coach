/**
 * AI Coach Lovelace card.
 *
 * Talks to the ai_coach integration over the Home Assistant WebSocket
 * connection. The backend identifies the user from the authenticated
 * connection, so every dashboard user sees only their own history.
 */

const CARD_VERSION = "0.1.0";
const CARD_TAG = "ai-coach-card";

const STYLES = `
  :host { display: block; }
  ha-card {
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 16px;
    border-bottom: 1px solid var(--divider-color);
  }
  .title {
    font-size: 1.2em;
    font-weight: 500;
    color: var(--primary-text-color);
  }
  .icon-button {
    background: none;
    border: none;
    cursor: pointer;
    color: var(--secondary-text-color);
    padding: 6px;
    border-radius: 50%;
    display: inline-flex;
  }
  .icon-button:hover { background: var(--secondary-background-color); }
  .icon-button[disabled] { opacity: 0.4; cursor: default; }
  .messages {
    flex: 1;
    overflow-y: auto;
    padding: 12px 16px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .empty {
    margin: auto;
    color: var(--secondary-text-color);
    text-align: center;
  }
  .bubble {
    max-width: 85%;
    padding: 8px 12px;
    border-radius: 14px;
    line-height: 1.4;
    white-space: pre-wrap;
    word-wrap: break-word;
  }
  .bubble.user {
    align-self: flex-end;
    background: var(--primary-color);
    color: var(--text-primary-color, #fff);
    border-bottom-right-radius: 4px;
  }
  .bubble.assistant {
    align-self: flex-start;
    background: var(--secondary-background-color);
    color: var(--primary-text-color);
    border-bottom-left-radius: 4px;
  }
  .bubble.assistant ha-markdown { white-space: normal; }
  .bubble .time {
    display: block;
    font-size: 0.7em;
    opacity: 0.7;
    margin-top: 4px;
    text-align: right;
  }
  .typing {
    align-self: flex-start;
    color: var(--secondary-text-color);
    font-style: italic;
    padding: 0 4px;
  }
  .error {
    margin: 0 16px 8px;
    padding: 8px 12px;
    border-radius: 8px;
    background: var(--error-color, #db4437);
    color: #fff;
    font-size: 0.9em;
  }
  .composer {
    display: flex;
    align-items: flex-end;
    gap: 8px;
    padding: 8px 12px 12px;
    border-top: 1px solid var(--divider-color);
  }
  textarea {
    flex: 1;
    resize: none;
    min-height: 40px;
    max-height: 120px;
    padding: 10px 12px;
    border-radius: 20px;
    border: 1px solid var(--divider-color);
    background: var(--card-background-color);
    color: var(--primary-text-color);
    font: inherit;
    box-sizing: border-box;
  }
  textarea:focus { outline: none; border-color: var(--primary-color); }
  .send {
    color: var(--primary-color);
  }
`;

class AICoachCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = null;
    this._hass = null;
    this._userId = null;
    this._messages = [];
    this._historyLoading = false;
    this._sending = false;
    this._error = null;
    this._built = false;
  }

  static getStubConfig() {
    return { title: "AI Coach" };
  }

  setConfig(config) {
    this._config = { title: "AI Coach", height: "400px", ...config };
    if (this._built) {
      this._els.title.textContent = this._config.title;
      this._els.messages.style.height = this._config.height;
    }
  }

  getCardSize() {
    return 7;
  }

  // HA calls this setter on every state change, so keep it cheap.
  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();

    const userId = hass.user?.id ?? null;
    if (userId !== this._userId) {
      this._userId = userId;
      this._messages = [];
      this._loadHistory();
    }
  }

  get hass() {
    return this._hass;
  }

  _build() {
    const root = this.shadowRoot;
    root.innerHTML = `
      <style>${STYLES}</style>
      <ha-card>
        <div class="header">
          <span class="title"></span>
          <button class="icon-button clear" title="Clear chat history">
            <ha-icon icon="mdi:delete-sweep-outline"></ha-icon>
          </button>
        </div>
        <div class="messages"></div>
        <div class="error" hidden></div>
        <div class="composer">
          <textarea rows="1" placeholder="Message your coach…"></textarea>
          <button class="icon-button send" title="Send">
            <ha-icon icon="mdi:send"></ha-icon>
          </button>
        </div>
      </ha-card>
    `;

    this._els = {
      title: root.querySelector(".title"),
      messages: root.querySelector(".messages"),
      error: root.querySelector(".error"),
      input: root.querySelector("textarea"),
      send: root.querySelector(".send"),
      clear: root.querySelector(".clear"),
    };

    this._els.title.textContent = this._config?.title ?? "AI Coach";
    this._els.messages.style.height = this._config?.height ?? "400px";

    this._els.send.addEventListener("click", () => this._send());
    this._els.clear.addEventListener("click", () => this._clear());
    this._els.input.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) {
        ev.preventDefault();
        this._send();
      }
    });
    this._els.input.addEventListener("input", () => this._autosize());

    this._built = true;
    this._renderMessages();
  }

  async _loadHistory() {
    if (!this._hass || this._historyLoading) return;
    this._historyLoading = true;
    this._setError(null);
    this._renderMessages();
    try {
      const result = await this._hass.callWS({ type: "ai_coach/history", limit: 100 });
      this._messages = result.messages;
    } catch (err) {
      this._setError(this._errorText(err, "Could not load chat history"));
    } finally {
      this._historyLoading = false;
      this._renderMessages();
    }
  }

  async _send() {
    const text = this._els.input.value.trim();
    if (!text || this._sending || !this._hass) return;

    const pending = {
      id: `pending-${Date.now()}`,
      role: "user",
      content: text,
      created_at: new Date().toISOString(),
    };
    this._messages = [...this._messages, pending];
    this._els.input.value = "";
    this._autosize();
    this._sending = true;
    this._setError(null);
    this._renderMessages();

    try {
      const result = await this._hass.callWS({
        type: "ai_coach/send_message",
        message: text,
      });
      this._messages = [
        ...this._messages.filter((m) => m !== pending),
        result.user_message,
        result.assistant_message,
      ];
    } catch (err) {
      // The backend stores the user message before calling the LLM, so it
      // stays in the list; only the reply is missing.
      this._setError(this._errorText(err, "The coach could not respond"));
    } finally {
      this._sending = false;
      this._renderMessages();
      this._els.input.focus();
    }
  }

  async _clear() {
    if (!this._hass || this._sending) return;
    if (!confirm("Delete your entire chat history with the coach?")) return;
    try {
      await this._hass.callWS({ type: "ai_coach/clear_history" });
      this._messages = [];
      this._setError(null);
    } catch (err) {
      this._setError(this._errorText(err, "Could not clear history"));
    }
    this._renderMessages();
  }

  _renderMessages() {
    if (!this._built) return;
    const container = this._els.messages;
    container.replaceChildren();

    if (this._historyLoading && this._messages.length === 0) {
      container.append(this._textEl("div", "empty", "Loading…"));
    } else if (this._messages.length === 0) {
      container.append(
        this._textEl("div", "empty", "Say hi to your coach to get started 💪")
      );
    }

    for (const msg of this._messages) {
      container.append(this._bubble(msg));
    }

    if (this._sending) {
      container.append(this._textEl("div", "typing", "Coach is typing…"));
    }

    this._els.send.disabled = this._sending;
    this._els.clear.disabled = this._sending || this._messages.length === 0;
    container.scrollTop = container.scrollHeight;
  }

  _bubble(msg) {
    const bubble = document.createElement("div");
    bubble.className = `bubble ${msg.role}`;

    if (msg.role === "assistant" && customElements.get("ha-markdown")) {
      // ha-markdown sanitises its input.
      const md = document.createElement("ha-markdown");
      md.breaks = true;
      md.content = msg.content;
      bubble.append(md);
    } else {
      bubble.append(document.createTextNode(msg.content));
    }

    if (msg.created_at) {
      bubble.append(this._textEl("span", "time", this._formatTime(msg.created_at)));
    }
    return bubble;
  }

  _textEl(tag, className, text) {
    const el = document.createElement(tag);
    el.className = className;
    el.textContent = text;
    return el;
  }

  _formatTime(iso) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    const locale = this._hass?.locale?.language ?? navigator.language;
    const sameDay = date.toDateString() === new Date().toDateString();
    return sameDay
      ? date.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" })
      : date.toLocaleString(locale, {
          day: "numeric",
          month: "short",
          hour: "2-digit",
          minute: "2-digit",
        });
  }

  _setError(text) {
    this._error = text;
    if (!this._built) return;
    this._els.error.hidden = !text;
    this._els.error.textContent = text ?? "";
  }

  _errorText(err, fallback) {
    if (err?.code === "not_loaded") return "AI Coach integration is not set up.";
    return err?.message ? `${fallback}: ${err.message}` : fallback;
  }

  _autosize() {
    const input = this._els.input;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
  }
}

if (!customElements.get(CARD_TAG)) {
  customElements.define(CARD_TAG, AICoachCard);

  window.customCards = window.customCards || [];
  window.customCards.push({
    type: CARD_TAG,
    name: "AI Coach",
    description: "Chat with your personal AI fitness coach.",
    preview: false,
  });

  console.info(
    `%c AI-COACH-CARD %c v${CARD_VERSION} `,
    "color: white; background: #03a9f4; font-weight: 700;",
    "color: #03a9f4; background: white; font-weight: 700;"
  );
}
