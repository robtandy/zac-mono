# Zac Agent Technical Overview

## Session Persistence

The Zac agent supports session persistence, allowing the conversation state to be saved on shutdown and reloaded on startup. This enables users to continue their work seamlessly after restarting the gateway.

### Key Components

1. **AgentClient**
   - The `AgentClient` class manages the conversation state, including:
     - `self._messages`: The conversation history.
     - `self._model`: The current model ID.
     - `self._system_prompt`: The system prompt.
   - Methods for persistence:
     - `save_session()`: Serializes the session state to a JSON file.
     - `reload_session()`: Deserializes the session state from a JSON file.

2. **Session File**
   - The session state is saved to `~/.zac/session.json` by default.
   - The file contains the following fields:
     - `model`: The current model ID.
     - `system_prompt`: The system prompt.
     - `messages`: The conversation history.

3. **Gateway Server**
   - The `run()` function in `server.py` saves the session state before stopping the agent.
   - The `AgentClient` reloads the session state after starting.

### How It Works

1. **Saving the Session**
   - When the gateway server receives a shutdown signal, it calls `agent.save_session()` to serialize the session state to `~/.zac/session.json`.

2. **Reloading the Session**
   - When the gateway server starts, the `AgentClient` calls `reload_session()` after initializing. This deserializes the session state from `~/.zac/session.json` and restores the conversation history, model, and system prompt.

3. **UI Updates**
   - The `Session` class broadcasts the reloaded conversation history to all connected clients, ensuring the UI reflects the current state.

### Customization

- **Session File Path**: The default session file path is `~/.zac/session.json`. This can be customized by passing a `session_file` parameter to the `AgentClient` constructor.

### Example Session File

```json
{
  "model": "anthropic/claude-sonnet-4",
  "system_prompt": "You are a helpful coding assistant.",
  "messages": [
    {
      "role": "user",
      "content": "Hello, agent!"
    },
    {
      "role": "assistant",
      "content": "Hello! How can I help you today?"
    }
  ]
}
```