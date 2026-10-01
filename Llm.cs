using System.Collections.Generic;
using System.Threading.Tasks;

namespace AiAgentDevelopment;

public enum Role { User, Assistant, Tool }

/// <summary>A single turn in the conversation. For tool results, ToolCallId links back to the call.</summary>
public record Message(Role Role, string Content, string? ToolName = null, string? ToolCallId = null, ToolCall? Call = null);

/// <summary>A tool the model may call: name, human description, and a JSON-schema string for its input.</summary>
public record ToolDef(string Name, string Description, string InputSchemaJson);

/// <summary>A model's request to invoke a tool.</summary>
public record ToolCall(string Id, string Name, string ArgsJson);

/// <summary>Either a final text answer, or a tool call to execute and feed back.</summary>
public record LlmResponse(string? Text, ToolCall? Call);

/// <summary>
/// Pluggable LLM backend. The agent only depends on this — swap <see cref="AnthropicClient"/>
/// (real API) for <see cref="MockLlmClient"/> (offline, deterministic) without touching the loop.
/// </summary>
public interface ILlmClient
{
    Task<LlmResponse> CompleteAsync(IReadOnlyList<Message> messages, IReadOnlyList<ToolDef> tools);
}
