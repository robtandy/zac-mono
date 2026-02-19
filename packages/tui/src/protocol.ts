// Client -> Gateway messages
export type ClientMessage =
  | { type: "prompt"; message: string }
  | { type: "steer"; message: string }
  | { type: "abort" };

// Gateway -> Client events
export type ServerEvent =
  | { type: "turn_start" }
  | { type: "text_delta"; delta: string }
  | { type: "tool_start"; tool_name: string; tool_call_id: string; args: Record<string, unknown> }
  | { type: "tool_update"; tool_call_id: string; tool_name: string; partial_result: string }
  | { type: "tool_end"; tool_call_id: string; tool_name: string; result: string; is_error: boolean }
  | { type: "turn_end" }
  | { type: "agent_end" }
  | { type: "error"; message: string };
