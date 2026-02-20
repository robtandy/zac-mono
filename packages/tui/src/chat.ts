import { execSync } from "child_process";
import { writeFileSync, mkdtempSync } from "fs";
import { join } from "path";
import { tmpdir } from "os";
import { TUI, ProcessTerminal, Editor, Image, Markdown, Text, Spacer, CombinedAutocompleteProvider, getCapabilities } from "@mariozechner/pi-tui";
import type { GatewayConnection } from "./connection.js";
import type { ServerEvent } from "./protocol.js";
import { editorTheme, imageTheme, markdownTheme, statusColor, statusBarColor, errorColor, userMsgColor, toolColor, toolDimColor, contextSystemColor, contextToolsColor, contextUserColor, contextAssistantColor, contextToolResultsColor, contextFreeColor, compactionColor } from "./theme.js";

const MAX_RESULT_LINES = 20;
const MAX_RESULT_CHARS = 4000;

export class ChatUI {
  private tui: TUI;
  private editor: Editor;
  private connection: GatewayConnection;
  private currentMarkdown: Markdown | null = null;
  private currentText = "";
  private currentToolMarkdown: Markdown | null = null;
  private currentToolText = "";
  private statusBar: Text;
  private inputQueuedDuringCompaction: string[] = [];
  private isCompacting = false;
  private screenshotDir: string | null = null;

  constructor(connection: GatewayConnection) {
    this.connection = connection;
    const terminal = new ProcessTerminal();
    this.tui = new TUI(terminal);

    this.editor = new Editor(this.tui, editorTheme);

    // Define slash commands for autocomplete
    const slashCommands = [
      { name: "abort", description: "Abort the current operation" },
      { name: "context", description: "Show context information" },
      { name: "compact", description: "Compact the conversation history" },
      { name: "reload", description: "Reload the agent and web packages" },
      { name: "search", description: "Search the web using DuckDuckGo" },
    ];

    // Create autocomplete provider for slash commands
    const autocompleteProvider = new CombinedAutocompleteProvider(
      slashCommands,
      process.cwd() // Base path for file completion
    );
    this.editor.setAutocompleteProvider(autocompleteProvider);

    this.editor.onSubmit = (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;

      if (trimmed === "/abort") {
        this.connection.send({ type: "abort" });
        this.insertBeforeEditor(new Text("[Abort sent]", 1, 0, statusColor));
        return;
      }

      if (trimmed === "/context") {
        this.connection.send({ type: "context_request" });
        return;
      }

      if (trimmed === "/compact") {
        this.connection.send({ type: "steer", message: "/compact" });
        return;
      }

      if (trimmed === "/reload") {
        this.connection.send({ type: "steer", message: "/reload" });
        return;
      }

      if (trimmed.startsWith("/search ")) {
        const query = trimmed.slice(8).trim();
        if (!query) {
          this.insertBeforeEditor(new Text("Error: No search query provided.", 1, 0, errorColor));
          return;
        }
        this.connection.send({ type: "prompt", message: `/search ${query}` });
        return;
      }

      if (trimmed.startsWith("!")) {
        const cmd = trimmed.slice(1).trim();
        if (cmd) this.runShellCommand(cmd);
        return;
      }

      if (this.isCompacting) {
        this.inputQueuedDuringCompaction.push(trimmed);
        this.insertBeforeEditor(new Text(`[Queued] > ${trimmed}`, 1, 0, statusColor));
        return;
      }

      // Send to gateway (user_message event will come back via broadcast)
      this.connection.send({ type: "prompt", message: trimmed });
      this.currentText = "";
      this.currentMarkdown = null;
    };



    this.tui.addChild(this.editor);
    const cwd = process.cwd();
    const dirName = cwd.split(/[\/]/).pop() || cwd;
    this.statusBar = new Text(`Ready | ${dirName}`, 1, 0, statusBarColor);
    this.tui.addChild(this.statusBar);
    this.tui.setFocus(this.editor);

    this.tui.addInputListener((data: string) => {
      if (data === "\x03") {
        if (this.editor.getText().trim()) {
          this.editor.setText("");
        } else {
          this.connection.send({ type: "abort" });
          this.insertBeforeEditor(new Text("[Abort sent]", 1, 0, statusColor));
        }
        return true;
      }
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
      case "user_message":
        this.insertBeforeEditor(new Text(`> ${event.message}`, 1, 0, userMsgColor));
        this.insertBeforeEditor(new Spacer(1));
        break;

      case "turn_start":
        this.currentText = "";
        this.currentMarkdown = null;
        this.setStatus("Thinking...");
        break;

      case "text_delta":
        this.currentText += event.delta;
        this.updateMarkdown();
        this.setStatus("Responding...");
        break;

      case "tool_start": {
        this.finalizeMarkdown();
        this.finalizeToolMarkdown();
        this.setStatus(`Running tool: ${event.tool_name}`);
        const header = this.formatToolHeader(event.tool_name, event.args);
        this.insertBeforeEditor(new Text(header, 1, 0, toolColor));
        if (event.tool_name === "edit") {
          const diff = this.formatEditDiff(event.args);
          if (diff) {
            const md = new Markdown("```diff\n" + diff + "\n```", 0, 0, markdownTheme);
            this.insertBeforeEditor(md);
          }
        }
        break;
      }

      case "tool_update": {
        this.currentToolText += event.partial_result;
        this.updateToolMarkdown();
        break;
      }

      case "tool_end": {
        this.finalizeToolMarkdown();
        if (event.result) {
          const truncated = this.truncateResult(event.result);
          const md = new Markdown("```\n" + truncated + "\n```", 0, 0, markdownTheme);
          this.insertBeforeEditor(md);
        }
        const status = event.is_error ? "error" : "done";
        const color = event.is_error ? errorColor : statusColor;
        this.insertBeforeEditor(new Text(`[${event.tool_name}: ${status}]`, 0, 0, color));
        break;
      }

      case "turn_end":
      case "agent_end":
        this.finalizeMarkdown();
        this.finalizeToolMarkdown();
        this.insertBeforeEditor(new Spacer(1));
        this.setStatus("Ready");
        break;

      case "error":
        this.finalizeMarkdown();
        this.finalizeToolMarkdown();
        this.insertBeforeEditor(new Text(`Error: ${event.message}`, 1, 0, errorColor));
        this.setStatus("Error");
        break;

      case "compaction_start":
        this.isCompacting = true;
        this.setStatus("Compacting context...");
        break;

      case "compaction_end": {
        this.isCompacting = false;
        // Clear all chat children (everything before editor)
        const children = this.tui.children;
        const editorIdx = children.indexOf(this.editor);
        children.splice(0, editorIdx);
        // Show compaction header
        const header = event.tokens_before > 0
          ? `[Compacted from ${event.tokens_before.toLocaleString()} tokens]`
          : "[Compaction complete]";
        this.insertBeforeEditor(new Text(header, 1, 0, compactionColor));
        // Show summary text
        if (event.summary) {
          this.insertBeforeEditor(new Markdown(event.summary, 0, 0, markdownTheme));
        }
        this.insertBeforeEditor(new Spacer(1));
        this.setStatus("Ready");
        this.tui.requestRender();
        // Flush queued input
        for (const msg of this.inputQueuedDuringCompaction) {
          this.connection.send({ type: "prompt", message: msg });
        }
        this.inputQueuedDuringCompaction = [];
        break;
      }

      case "reload_start":
        this.setStatus("Reloading...");
        this.insertBeforeEditor(new Text("[Reloading agent and web packages...]", 1, 0, compactionColor));
        break;

      case "reload_end": {
        const color = event.success ? compactionColor : errorColor;
        this.insertBeforeEditor(new Text(`[${event.message}]`, 0, 0, color));
        this.insertBeforeEditor(new Spacer(1));
        this.setStatus("Ready");
        break;
      }

      case "context_info":
        this.renderContextBar(event);
        break;

      case "canvas_update": {
        const label = event.url ? `Navigated to ${event.url}` : "HTML updated";
        this.insertBeforeEditor(new Text(`[Canvas: ${label}]`, 0, 0, statusColor));
        break;
      }

      case "canvas_screenshot": {
        const canInline = getCapabilities().images && !process.env.TMUX;
        if (canInline) {
          this.insertBeforeEditor(new Text("[Canvas screenshot]", 0, 0, statusColor));
          const img = new Image(event.image_data, "image/png", imageTheme, { maxWidthCells: 80 });
          this.insertBeforeEditor(img);
        } else {
          // No image protocol or inside tmux — save to temp file
          const dir = this.screenshotDir ?? (this.screenshotDir = mkdtempSync(join(tmpdir(), "zac-canvas-")));
          const file = join(dir, `screenshot-${Date.now()}.png`);
          writeFileSync(file, Buffer.from(event.image_data, "base64"));
          this.insertBeforeEditor(new Text(`[Canvas screenshot saved: ${file}]`, 0, 0, statusColor));
        }
        break;
      }

      case "canvas_dismiss":
        this.insertBeforeEditor(new Text("[Canvas dismissed]", 0, 0, statusColor));
        break;
    }
  }

  private formatToolHeader(toolName: string, args: Record<string, unknown>): string {
    switch (toolName) {
      case "bash": {
        const cmd = typeof args.command === "string" ? args.command : "";
        const display = cmd.length > 200 ? cmd.slice(0, 200) + "..." : cmd;
        return `[bash] $ ${display}`;
      }
      case "read": {
        const path = typeof args.file_path === "string" ? args.file_path : "";
        let detail = path;
        if (args.offset || args.limit) {
          const parts: string[] = [];
          if (args.offset) parts.push(`offset=${args.offset}`);
          if (args.limit) parts.push(`limit=${args.limit}`);
          detail += ` (${parts.join(", ")})`;
        }
        return `[read] ${detail}`;
      }
      case "write": {
        const path = typeof args.file_path === "string" ? args.file_path : "";
        const content = typeof args.content === "string" ? args.content : "";
        return `[write] ${path} (${content.length} bytes)`;
      }
      case "edit": {
        const path = typeof args.file_path === "string" ? args.file_path : "";
        return `[edit] ${path}`;
      }
      default: {
        const summary = JSON.stringify(args);
        const display = summary.length > 200 ? summary.slice(0, 200) + "..." : summary;
        return `[${toolName}] ${display}`;
      }
    }
  }

  private formatEditDiff(args: Record<string, unknown>): string | null {
    const oldText = typeof args.old_text === "string" ? args.old_text : "";
    const newText = typeof args.new_text === "string" ? args.new_text : "";
    if (!oldText && !newText) return null;

    const lines: string[] = [];
    for (const line of oldText.split("\n")) {
      lines.push(`- ${line}`);
    }
    for (const line of newText.split("\n")) {
      lines.push(`+ ${line}`);
    }
    return lines.join("\n");
  }

  private truncateResult(result: string): string {
    let text = result;
    if (text.length > MAX_RESULT_CHARS) {
      text = text.slice(0, MAX_RESULT_CHARS) + "\n... (truncated)";
    }
    const lines = text.split("\n");
    if (lines.length > MAX_RESULT_LINES) {
      return lines.slice(0, MAX_RESULT_LINES).join("\n") + "\n... (truncated)";
    }
    return text;
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

  private updateToolMarkdown(): void {
    if (!this.currentToolText) return;

    const display = "```\n" + this.truncateResult(this.currentToolText) + "\n```";
    if (this.currentToolMarkdown) {
      this.currentToolMarkdown.setText(display);
    } else {
      this.currentToolMarkdown = new Markdown(display, 0, 0, markdownTheme);
      this.insertBeforeEditor(this.currentToolMarkdown);
    }
    this.tui.requestRender();
  }

  private finalizeMarkdown(): void {
    if (this.currentMarkdown) {
      this.currentMarkdown = null;
      this.currentText = "";
    }
  }

  private finalizeToolMarkdown(): void {
    if (this.currentToolMarkdown) {
      this.currentToolMarkdown = null;
      this.currentToolText = "";
    }
  }

  private insertBeforeEditor(component: any): void {
    const children = this.tui.children;
    const editorIdx = children.indexOf(this.editor);
    children.splice(editorIdx, 0, component);
    this.tui.requestRender();
  }

  private runShellCommand(cmd: string): void {
    this.insertBeforeEditor(new Text(`! ${cmd}`, 1, 0, userMsgColor));
    try {
      const output = execSync(cmd, {
        encoding: "utf-8",
        stdio: ["pipe", "pipe", "pipe"],
        timeout: 30_000,
      });
      if (output.trim()) {
        const md = new Markdown("```\n" + output.trimEnd() + "\n```", 0, 0, markdownTheme);
        this.insertBeforeEditor(md);
      }
    } catch (err: any) {
      // execSync throws on non-zero exit; stdout/stderr are on the error object
      const output = (err.stdout ?? "") + (err.stderr ?? "");
      if (output.trim()) {
        const md = new Markdown("```\n" + output.trimEnd() + "\n```", 0, 0, markdownTheme);
        this.insertBeforeEditor(md);
      } else {
        this.insertBeforeEditor(new Text(err.message ?? "Command failed", 0, 0, errorColor));
      }
    }
    this.insertBeforeEditor(new Spacer(1));
  }

  private renderContextBar(data: { system: number; tools: number; user: number; assistant: number; tool_results: number; context_window: number }): void {
    const segments = [
      { label: "System", tokens: data.system, color: contextSystemColor },
      { label: "Tools", tokens: data.tools, color: contextToolsColor },
      { label: "User", tokens: data.user, color: contextUserColor },
      { label: "Assistant", tokens: data.assistant, color: contextAssistantColor },
      { label: "Tool results", tokens: data.tool_results, color: contextToolResultsColor },
    ];

    const used = segments.reduce((sum, s) => sum + s.tokens, 0);
    const free = Math.max(0, data.context_window - used);
    const pct = data.context_window > 0 ? Math.round((used / data.context_window) * 100) : 0;

    const barWidth = Math.max(20, (process.stdout.columns || 80) - 4);

    // Build bar
    let bar = "";
    let remaining = barWidth;
    for (const seg of segments) {
      const cols = data.context_window > 0 ? Math.round((seg.tokens / data.context_window) * barWidth) : 0;
      const clamped = Math.min(cols, remaining);
      if (clamped > 0) {
        bar += seg.color("\u2588".repeat(clamped));
        remaining -= clamped;
      }
    }
    if (remaining > 0) {
      bar += contextFreeColor("\u2588".repeat(remaining));
    }

    // Legend
    const legend = segments
      .map((s) => s.color("\u25A0") + " " + s.label)
      .concat([contextFreeColor("\u25A0") + " Free"])
      .join("  ");

    // Token summary
    const summary = `Used: ${used.toLocaleString()} / ${data.context_window.toLocaleString()} tokens (${pct}%)`;

    this.insertBeforeEditor(new Text(bar, 1, 0));
    this.insertBeforeEditor(new Text(legend, 0, 0));
    this.insertBeforeEditor(new Text(summary, 0, 0, statusColor));
    this.insertBeforeEditor(new Spacer(1));
  }

  private setStatus(text: string): void {
    const cwd = process.cwd();
    const dirName = cwd.split(/[\/]/).pop() || cwd;
    this.statusBar.setText(`${text} | ${dirName}`);
    this.tui.requestRender();
  }

  setConnected(connected: boolean): void {
    if (connected) {
      this.setStatus("Ready");
    } else {
      this.setStatus("Reconnecting...");
    }
  }

  start(): void {
    this.tui.start();
  }

  stop(): void {
    this.tui.stop();
  }
}
