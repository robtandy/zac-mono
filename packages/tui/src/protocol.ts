// Client -> Gateway messages
export type ClientMessage =
  | { type: "prompt"; message: string }
  | { type: "steer"; message: string }
  | { type: "abort" }
  | { type: "context_request" }
  | { type: "model_list_request" };

// Gateway -> Client events
export type ServerEvent =
  | { type: "user_message"; message: string }
  | { type: "turn_start" }
  | { type: "text_delta"; delta: string }
  | { type: "tool_start"; tool_name: string; tool_call_id: string; args: Record<string, unknown> }
  | { type: "tool_update"; tool_call_id: string; tool_name: string; partial_result: string }
  | { type: "tool_end"; tool_call_id: string; tool_name: string; result: string; is_error: boolean }
  | { type: "turn_end" }
  | { type: "agent_end" }
  | { type: "error"; message: string }
  | { type: "context_info"; system: number; tools: number; user: number; assistant: number; tool_results: number; context_window: number }
  | { type: "compaction_start" }
  | { type: "compaction_end"; summary: string; tokens_before: number }
  | { type: "reload_start" }
  | { type: "reload_end"; success: boolean; message: string }
  | { type: "canvas_update"; html?: string; url?: string }
  | { type: "canvas_screenshot"; image_data: string }
  | { type: "canvas_dismiss" }
  | { type: "model_list"; models: { id: string; name: string; description: string }[]; current: string }
  | { type: "model_set"; model: string };
