# AGENTS.md

This document describes the key packages, abstractions, and conventions in the project. It is intended to help AI agents and developers navigate and understand the codebase.

---

## `action-system` Package

### Overview
The `action-system` package is a framework for managing **actions**, **permissions**, and **execution** in a controlled and auditable way. It is designed to:
- Allow **handlers** (e.g., tools, APIs, or services) to register actions they can perform.
- Enforce **fine-grained permissions** for actions, with support for scoped and time-limited grants.
- Execute actions immediately if permitted, or **enqueue them for approval** if not.
- Persist **action requests** and **permission grants** in an SQLite database.
- Emit **events** for integration with other systems (e.g., notifications, logging, or UI updates).

---

### Key Abstractions

#### 1. **`ActionSystem` (`core.py`)**
- The **main entry point** for the system.
- Manages:
  - **Handler registration**: Register handlers that define actions and permissions.
  - **Action requests**: Execute actions immediately if permitted, or enqueue them for approval.
  - **Permission management**: Check, grant, or revoke permissions.
  - **Event emission**: Emit events for actions (e.g., `ACTION_ENQUEUED`, `ACTION_COMPLETED`, `PERMISSION_NEEDED`).
- Uses:
  - `Store` for persistence.
  - `PermissionManager` for permission logic.
  - `EventBus` for event handling.

#### 2. **`ActionHandler` (`handler.py`)**
- **Base class** for defining handlers (e.g., tools, APIs, or services).
- Subclasses must define:
  - `handler_id`: Unique identifier for the handler.
  - `name`: Human-readable name.
  - `permissions`: List of `PermissionDef` objects defining the permissions required for actions.
  - `execute()`: Method to perform the action.
- Provides:
  - `get_required_permission()`: Determines the permission required for an action.
  - `render_request()`: Customizes how action requests appear in the UI.
  - `as_tool_schema()`: Returns a tool definition for AI agent registration.

#### 3. **`PermissionDef` and `PermissionGrant` (`models.py`)**
- **`PermissionDef`**: Defines a permission (e.g., `"send_email"`), including its name, description, and accepted scope parameters (e.g., `{"recipient": "Email address to send to"}`).
- **`PermissionGrant`**: Represents a granted permission, including its scope, expiration, and who granted it.

#### 4. **`PermissionManager` (`permissions.py`)**
- Manages **permission grants** and **checks**. 
- Methods:
  - `check()`: Checks if a permission is granted for a given scope.
  - `grant()`: Grants a permission with optional scope and expiration.
  - `revoke()`: Revokes a permission grant.
  - `get_all_grants()`: Returns all granted permissions.

#### 5. **`Store` (`store.py`)**
- **SQLite-backed persistence** for:
  - **Permission grants**: Stores granted permissions.
  - **Action requests**: Stores action requests, their status, and results.
- Methods:
  - `save_grant()`: Saves a permission grant.
  - `get_grants()`: Retrieves grants for a handler and permission.
  - `save_action()`: Saves an action request.
  - `get_action()`: Retrieves an action request by ID.
  - `get_pending_actions()`: Retrieves all pending actions.

#### 6. **`EventBus` (`notifications.py`)**
- Emits **events** for actions and permissions (e.g., `ACTION_ENQUEUED`, `ACTION_COMPLETED`, `PERMISSION_NEEDED`).
- Allows other systems to **subscribe** to events (e.g., for logging, notifications, or UI updates).

#### 7. **`ActionRequest` and `ActionResult` (`models.py`)**
- **`ActionRequest`**: Represents a request to execute an action, including its parameters, status, and required permission.
- **`ActionResult`**: Represents the result of an action request, including its status, result, and error (if any).

---

### Workflow
1. **Register Handlers**: Handlers (e.g., email, file operations) register their actions and permissions with the `ActionSystem`.
2. **Request Actions**: A user or agent requests an action (e.g., `send_email`). The `ActionSystem` checks if the required permission is granted.
3. **Execute or Enqueue**:
   - If the permission is granted, the action is executed immediately.
   - If not, the action is enqueued for approval, and an event (`PERMISSION_NEEDED`) is emitted.
4. **Approve Actions**: A human (or automated system) approves the action, granting the required permission. The `ActionSystem` then executes the action.
5. **Persist State**: All action requests and permission grants are persisted in SQLite.

---

### Key Features
- **Fine-Grained Permissions**: Permissions can be scoped (e.g., `{"recipient": "alice@example.com"}`) and time-limited (e.g., expire after 1 hour).
- **Event-Driven**: Events are emitted for key actions (e.g., `ACTION_ENQUEUED`, `ACTION_COMPLETED`), allowing integration with other systems.
- **Persistence**: All state (actions, permissions) is persisted in SQLite.
- **Extensible**: Handlers can be added for any action (e.g., sending emails, reading files, executing commands).

---

### Example Use Case
1. An **email handler** registers with the `ActionSystem`, defining a `send_email` action and a `send_email` permission.
2. An **agent** requests to send an email to `alice@example.com`.
3. The `ActionSystem` checks if the `send_email` permission is granted for `{"recipient": "alice@example.com"}`.
   - If granted, the email is sent immediately.
   - If not, the action is enqueued, and a `PERMISSION_NEEDED` event is emitted.
4. A **human** approves the action, granting the `send_email` permission for `{"recipient": "alice@example.com"}`.
5. The `ActionSystem` executes the action and emits an `ACTION_COMPLETED` event.

---

## What is Zac?

### Overview
**Zac** is the primary interface for interacting with this project. It is a **command-line tool** that:
- Manages the **gateway** (a WebSocket server for agent sessions).
- Launches the **TUI** (Terminal User Interface) for real-time interaction with AI agents.
- Provides an **ephemeral gateway** for safe testing and development.

Zac is designed to be simple, self-contained, and easy to use. It handles dependency management (e.g., auto-installing Node.js packages for the TUI) and ensures a smooth user experience.

---

## CLI Package (`packages/cli`)

### Overview
The CLI package provides the `zac` command-line interface. It:
- Manages the **gateway daemon** (start, stop, restart, status).
- Launches the **TUI** for real-time interaction with AI agents.
- Automatically handles dependencies (e.g., `npm install` for the TUI).

### Key Files
- **`src/cli/main.py`**: Entry point, argument parsing, command dispatch
- **`src/cli/tui.py`**: Launches the TUI via `npx tsx`, auto-installs dependencies
- **`src/cli/daemon.py`**: Gateway daemon management (start, stop, restart, status)
- **`src/cli/paths.py`**: Path discovery, finds repo root and standard paths

### How It Works
1. User runs `zac` command
2. CLI starts the gateway daemon (if not running)
3. CLI launches the TUI which connects to the gateway

### Auto-Install Feature
The CLI automatically runs `npm install` in the TUI directory if `node_modules` is missing. This ensures users don't need to manually install Node.js dependencies on first run.

---

## Gateway Package (`packages/gateway`)

### Overview
The gateway is a WebSocket server that manages agent sessions and exposes the web UI.

### Key Files
- **`src/gateway/__main__.py`**: Entry point, auto-discovers web UI, auto-installs dependencies
- **`src/gateway/server.py`**: Main server implementation
- **`src/gateway/session.py`**: Client session management, handles messages, `/reload` command

### Web UI Integration
- The gateway serves the web UI from `packages/web/dist/` (pre-built)
- On `/reload` command, it rebuilds the web package via `npm run build`
- Automatically runs `npm install` in `packages/web` if `node_modules` is missing

### Session Management
- Each connected WebSocket client is bound to an `AgentClient` instance
- Handles commands: `prompt`, `steer`, `abort`, `context_request`, `model_list_request`, `model_info_request`
- Special `/reload` command hot-reloads Python agent modules and rebuilds web UI
- Saves model and reasoning effort to config for persistence across restarts

### Config Persistence
- The agent persists `model` and `reasoning_effort` to `~/.zac/agent_config.json`
- On startup, loads saved values and uses them automatically
- Config is saved when `/model` or `/reasoning` commands are executed

---

## TUI Package (`packages/tui`)

### Overview
The Terminal User Interface - a Node.js/TypeScript app that connects to the gateway. It provides a real-time chat interface for interacting with AI agents, including a **status bar** at the bottom of the screen.

### Key Details
- Entry point: `packages/tui/src/index.ts`
- Runs via `npx tsx` (TypeScript executor)
- Connects to gateway via WebSocket (URL in `ZAC_GATEWAY_URL` env var)

### Status Bar
The status bar is implemented in `packages/tui/src/chat.ts` and displays:
- **status**: Dynamic text (e.g., "Ready", "Thinking...").
- **pwd**: Current working directory.
- **reasoning**: Reasoning effort (e.g., "xhigh").
- **model**: Current model (e.g., "claude-3.5-sonnet").

The status bar uses the `setStatus` method to update its content. It relies on ANSI escape codes to create consistent styling across all labels in tmux.

#### Testing in tmux
Always test the TUI in a `tmux` session to avoid taking over the terminal. Use the following workflow:

```bash
# Start a tmux session
tmux new -s tui-test

# Run the TUI
cd /root/zac-dev
.venv/bin/zac

# Detach from tmux
Ctrl+b d

# Reattach to tmux
tmux attach -t tui-test

# Kill the tmux session when done
tmux kill-session -t tui-test
```

---

## Web Package (`packages/web`)

### Overview
The web UI served by the gateway. Pre-built files in `dist/` are committed to the repo.

### Key Details
- Entry: `packages/web/src/index.ts`
- Build output: `packages/web/dist/`
- On `/reload`, the gateway rebuilds this package

---

## Conventions
- **Surgical Changes**: Only modify what is necessary to fulfill a task. Avoid refactoring unrelated code.
- **Simplicity First**: Write the minimum code required to solve the problem. Avoid speculative features or abstractions.
- **Event-Driven**: Use events to integrate systems (e.g., logging, notifications, UI updates).
- **Persistence**: Use SQLite for persisting state (e.g., actions, permissions).

---

## Development Setup

### Two-Directory Model
- **`/root/zac-dev`**: Development directory where changes are made and tested.
- **`/root/zac-run`**: Stable production directory - the version you run for daily use.

### Testing the TUI in tmux
To test the TUI in an isolated environment, use the following workflow:

1. **Start a tmux session** for testing:
   ```bash
   tmux new -s tui-test
   ```

2. **Run the TUI with an ephemeral gateway**:
   ```bash
   cd /root/zac-dev
   .venv/bin/zac
   ```
   - This automatically starts an ephemeral gateway for the TUI session.
   - The TUI will launch and connect to the gateway.

3. **Interact with the TUI**:
   - Type messages or commands (e.g., `hello world`) to test functionality.
   - Observe the output and behavior.

4. **Detach from the tmux session** (optional):
   - Press `Ctrl+b` followed by `d` to detach without stopping the TUI.

5. **Reattach to the tmux session** (optional):
   ```bash
   tmux attach -t tui-test
   ```

6. **Kill the tmux session** when done:
   ```bash
   tmux kill-session -t tui-test
   ```
   - This stops the TUI and the ephemeral gateway.

### Testing Workflow (for AI agent)
To test changes safely, use tmux to run an **ephemeral gateway** and TUI. This avoids killing the production gateway.

#### ⚠️ Warning
Do **not** use `pkill -f "python.*gateway"` to kill the gateway during testing. This can kill the **production gateway** if it is running. Instead, use the ephemeral gateway workflow below.

#### Ephemeral Gateway Workflow
1. **Start a tmux session**:
   ```bash
   tmux new -s zac-dev
   ```

2. **Run the TUI using `.venv/bin/zac`**:
   This command automatically starts an **ephemeral gateway** for the TUI session:
   ```bash
   cd /root/zac-dev
   .venv/bin/zac
   ```
   - The gateway will run on a default port (e.g., `8765`).
   - The TUI will connect to this gateway automatically.
   - The gateway will shut down when the TUI session ends.

   If you need to customize the gateway (e.g., port or log level), you can run it manually:
   ```bash
   source /root/zac-dev/.venv/bin/activate
   cd /root/zac-dev
   python -m gateway --no-tls --port 8765 --log-level debug
   ```
   Then, in another tmux pane, run the TUI:
   ```bash
   cd /root/zac-dev/packages/tui
   ZAC_GATEWAY_URL=ws://localhost:8765 npx tsx src/index.ts
   ```

3. **Test your changes**:
   Interact with the TUI to verify your changes work as expected.

### Key Commands
- **Start TUI with ephemeral gateway**: `.venv/bin/zac`
- **Gateway logs**: Check the terminal where the gateway is running.
- **Kill ephemeral gateway**: Close the TUI or use `Ctrl+C` in the gateway terminal.
- **Restart**: Stop and start the gateway or TUI again.

### Running Tests
```bash
cd /root/zac-dev/packages/gateway
pytest tests/ -v
```
