import WebSocket from "ws";
import type { ClientMessage, ServerEvent } from "./protocol.js";

export interface GatewayConnectionOptions {
  url: string;
  onEvent: (event: ServerEvent) => void;
  onConnect?: () => void;
  onDisconnect?: () => void;
}

export class GatewayConnection {
  private ws: WebSocket | null = null;
  private url: string;
  private onEvent: (event: ServerEvent) => void;
  private onConnect?: () => void;
  private onDisconnect?: () => void;
  private shouldReconnect = true;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(options: GatewayConnectionOptions) {
    this.url = options.url;
    this.onEvent = options.onEvent;
    this.onConnect = options.onConnect;
    this.onDisconnect = options.onDisconnect;
  }

  connect(): void {
    this.shouldReconnect = true;
    this._connect();
  }

  private _connect(): void {
    this.ws = new WebSocket(this.url);

    this.ws.on("open", () => {
      this.onConnect?.();
    });

    this.ws.on("message", (data) => {
      try {
        const event = JSON.parse(data.toString()) as ServerEvent;
        this.onEvent(event);
      } catch {
        // Ignore malformed messages
      }
    });

    this.ws.on("close", () => {
      this.onDisconnect?.();
      this._scheduleReconnect();
    });

    this.ws.on("error", () => {
      // Error will be followed by close event
    });
  }

  private _scheduleReconnect(): void {
    if (!this.shouldReconnect) return;
    this.reconnectTimer = setTimeout(() => {
      this._connect();
    }, 2000);
  }

  send(message: ClientMessage): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message));
    }
  }

  disconnect(): void {
    this.shouldReconnect = false;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.ws?.close();
    this.ws = null;
  }
}
