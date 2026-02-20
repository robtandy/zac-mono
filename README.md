# Zac

**Zac** is a personal AI coding assistant designed for seamless, real-time interaction. It combines a terminal-friendly agent with a zero-trust action system to safely execute tasks on your behalf.

---

## 🚀 Features

- **Real-time interaction**: Stream responses directly in your terminal or browser.
- **Zero-trust actions**: Granular permissions ensure the agent only does what you allow.
- **Multi-client support**: Use the terminal UI (`tui`) or web interface (`web`).
- **Manual compaction**: Free up context space with the `/compact` command.
- **Tool integration**: Extend functionality with custom tools (e.g., file editing, bash commands).

---

## 🔧 Architecture

```
┌─────────┐  ┌─────────┐
│   TUI   │  │   Web   │   Clients (TypeScript)
└────┬────┘  └────┬────┘
     │ WebSocket  │
     └─────┬──────┘
           │
     ┌─────▼─────┐
     │  Gateway  │   Python (WebSocket server)
     └─────┬─────┘
           │ stdin/stdout (JSON-RPC)
     ┌─────▼─────┐
     │   Agent   │   Python (LLM wrapper)
     └───────────┘
```

### Data Flow
1. User sends a message via the **TUI** or **Web UI**.
2. The **Gateway** forwards the message to the **Agent**.
3. The **Agent** streams responses back to all connected clients in real time.

---

## 📦 Packages

| Package          | Language       | Path                     | Purpose                                                                 |
|------------------|----------------|--------------------------|-------------------------------------------------------------------------|
| **agent**        | Python         | `packages/agent/`        | Async LLM wrapper with tool execution and context management.           |
| **gateway**      | Python         | `packages/gateway/`      | WebSocket server bridging clients to the agent.                        |
| **tui**          | TypeScript     | `packages/tui/`          | Terminal-based chat client.                                             |
| **web**          | TypeScript     | `packages/web/`          | Browser-based chat client.                                              |
| **action-system**| Python         | `packages/action-system/`| Zero-trust permission system for agent actions (e.g., file modifications). |

---

## 🔌 Protocol

### Client → Gateway
```typescript
type ClientMessage =
  | { type: "prompt"; message: string }   // Send a user message
  | { type: "steer"; message: string }    // Redirect the agent mid-execution
  | { type: "abort" }                     // Cancel the current execution
```

### Gateway → Client
```typescript
type ServerEvent =
  | { type: "turn_start" }
  | { type: "text_delta"; delta: string } // Streaming response
  | { type: "tool_start"; tool_name: string; tool_call_id: string; args: Record<string, unknown> }
  | { type: "tool_end"; tool_call_id: string; result: string; is_error: boolean }
  | { type: "turn_end" }
  | { type: "error"; message: string }
  | { type: "compaction_start" }          // Context compaction started
  | { type: "compaction_end"; summary: string; tokens_before: number } // Compaction complete
```

---

## 🛠️ Development Setup

### Prerequisites
- Python 3.11+
- Node.js 20+
- [`uv`](https://docs.astral.sh/uv/) (Python package manager)

### Install Dependencies
```bash
# Python packages (from repo root)
uv sync

# TUI
cd packages/tui && npm install

# Web
cd packages/web && npm install
```

### Run Tests
```bash
# All Python tests
uv run pytest

# Specific package
uv run pytest packages/agent/tests/
```

### Run the System
```bash
# 1. Start the gateway
uv run python -m gateway --debug

# 2a. Connect via TUI (in another terminal)
cd packages/tui && npm start

# 2b. Or build and serve the web UI
cd packages/web && npm run build
uv run python -m gateway --web-dir packages/web/dist
# Open http://localhost:8765 in a browser
```

---

## 📂 Repository Layout

```
zac/
├── pyproject.toml                 # uv workspace config
├── uv.lock                        # Python dependencies
├── packages/
│   ├── agent/                     # Agent package (LLM wrapper)
│   │   ├── src/agent/
│   │   │   ├── client.py          # AgentClient (main interface)
│   │   │   ├── events.py          # Event definitions
│   │   │   └── tools.py           # Built-in tools (e.g., file editing)
│   │   └── tests/
│   ├── gateway/                   # WebSocket server
│   │   ├── src/gateway/
│   │   │   ├── server.py          # WebSocket and HTTP server
│   │   │   └── session.py         # Client-agent session management
│   ├── tui/                       # Terminal UI
│   │   ├── src/
│   │   │   ├── chat.ts            # Terminal rendering
│   │   │   └── connection.ts      # WebSocket client
│   ├── web/                       # Web UI
│   │   ├── src/
│   │   │   ├── main.ts            # Entry point
│   │   │   └── chat.ts            # DOM rendering
│   └── action-system/             # Zero-trust permission system
│       ├── src/action_system/
│       │   ├── core.py            # ActionSystem (main class)
│       │   └── permissions.py     # Permission management
```

---

## 🤝 Acknowledgments

This project was developed with assistance from **Zac**, an AI coding assistant.

Special thanks to the **pi project** for its excellent **`pi-tui`** library, which powers the terminal UI client.