import { TUI, ProcessTerminal, Editor, Markdown, Text, Spacer } from "@mariozechner/pi-tui";
import type { GatewayConnection } from "./connection.js";
import type { ServerEvent } from "./protocol.js";
import { editorTheme, markdownTheme, statusColor, errorColor, userMsgColor } from "./theme.js";

export class ChatUI {
  private tui: TUI;
  private editor: Editor;
  private connection: GatewayConnection;
  private currentMarkdown: Markdown | null = null;
  private currentText = "";

  constructor(connection: GatewayConnection) {
    this.connection = connection;
    const terminal = new ProcessTerminal();
    this.tui = new TUI(terminal);

    this.editor = new Editor(this.tui, editorTheme);

    this.editor.onSubmit = (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;

      if (trimmed === "/abort") {
        this.connection.send({ type: "abort" });
        this.insertBeforeEditor(new Text("[Abort sent]", 1, 0, statusColor));
        return;
      }

      // Show user message
      this.insertBeforeEditor(new Text(`> ${trimmed}`, 1, 0, userMsgColor));
      this.insertBeforeEditor(new Spacer(1));

      // Send to gateway
      this.connection.send({ type: "prompt", message: trimmed });
      this.currentText = "";
      this.currentMarkdown = null;
    };

    this.tui.addChild(this.editor);
    this.tui.setFocus(this.editor);

    this.tui.addInputListener((data: string) => {
      if (data === "\x04") {
        this.connection.disconnect();
        this.tui.stop();
        process.exit(0);
      }
      return undefined;
    });
  }

  handleEvent(event: ServerEvent): void {
    switch (event.type) {
      case "turn_start":
        this.currentText = "";
        this.currentMarkdown = null;
        break;

      case "text_delta":
        this.currentText += event.delta;
        this.updateMarkdown();
        break;

      case "tool_start":
        this.finalizeMarkdown();
        this.insertBeforeEditor(new Text(`[Tool: ${event.tool_name}]`, 1, 0, statusColor));
        break;

      case "tool_end": {
        const status = event.is_error ? "error" : "done";
        const color = event.is_error ? errorColor : statusColor;
        this.insertBeforeEditor(new Text(`[Tool ${event.tool_name}: ${status}]`, 1, 0, color));
        break;
      }

      case "turn_end":
      case "agent_end":
        this.finalizeMarkdown();
        this.insertBeforeEditor(new Spacer(1));
        break;

      case "error":
        this.finalizeMarkdown();
        this.insertBeforeEditor(new Text(`Error: ${event.message}`, 1, 0, errorColor));
        break;
    }
  }

  private updateMarkdown(): void {
    if (!this.currentText) return;

    if (this.currentMarkdown) {
      // Update existing markdown in-place
      this.currentMarkdown.setText(this.currentText);
    } else {
      // Create new markdown component and insert before editor
      this.currentMarkdown = new Markdown(this.currentText, 1, 0, markdownTheme);
      this.insertBeforeEditor(this.currentMarkdown);
    }
    this.tui.requestRender();
  }

  private finalizeMarkdown(): void {
    if (this.currentMarkdown) {
      this.currentMarkdown = null;
      this.currentText = "";
    }
  }

  private insertBeforeEditor(component: any): void {
    const children = this.tui.children;
    const editorIdx = children.indexOf(this.editor);
    children.splice(editorIdx, 0, component);
    this.tui.requestRender();
  }

  start(): void {
    this.tui.start();
  }

  stop(): void {
    this.tui.stop();
  }
}
