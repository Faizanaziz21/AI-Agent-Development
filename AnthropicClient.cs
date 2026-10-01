using System;
using System.Collections.Generic;
using System.Linq;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading.Tasks;

namespace AiAgentDevelopment;

/// <summary>
/// Real LLM backend: Anthropic Messages API with native tool use. Reads the API key from the
/// ANTHROPIC_API_KEY environment variable. Our <see cref="Message"/> list is translated to the
/// API's content-block format (text / tool_use / tool_result), and the response is mapped back
/// to a <see cref="LlmResponse"/> (a tool_use block becomes a <see cref="ToolCall"/>).
/// </summary>
public sealed class AnthropicClient : ILlmClient
{
    private static readonly HttpClient Http = new();
    private readonly string _apiKey;
    private readonly string _model;

    public AnthropicClient(string? model = null)
    {
        _apiKey = Environment.GetEnvironmentVariable("ANTHROPIC_API_KEY")
                  ?? throw new InvalidOperationException("ANTHROPIC_API_KEY is not set.");
        _model = model ?? "claude-sonnet-5-5";
    }

    public async Task<LlmResponse> CompleteAsync(IReadOnlyList<Message> messages, IReadOnlyList<ToolDef> tools)
    {
        var apiMessages = messages.Select(ToApiMessage).ToArray();
        var apiTools = tools.Select(t => (object)new
        {
            name = t.Name,
            description = t.Description,
            input_schema = JsonNode.Parse(t.InputSchemaJson)
        }).ToArray();

        var body = new { model = _model, max_tokens = 1024, tools = apiTools, messages = apiMessages };

        using var req = new HttpRequestMessage(HttpMethod.Post, "https://api.anthropic.com/v1/messages")
        {
            Content = new StringContent(JsonSerializer.Serialize(body), Encoding.UTF8, "application/json")
        };
        req.Headers.Add("x-api-key", _apiKey);
        req.Headers.Add("anthropic-version", "2023-06-01");

        using var resp = await Http.SendAsync(req);
        string json = await resp.Content.ReadAsStringAsync();
        if (!resp.IsSuccessStatusCode)
            throw new HttpRequestException($"Anthropic API {(int)resp.StatusCode}: {json}");

        using var doc = JsonDocument.Parse(json);
        var content = doc.RootElement.GetProperty("content");

        var text = new StringBuilder();
        foreach (var block in content.EnumerateArray())
        {
            string type = block.GetProperty("type").GetString() ?? "";
            if (type == "tool_use")
            {
                return new LlmResponse(null, new ToolCall(
                    block.GetProperty("id").GetString() ?? "",
                    block.GetProperty("name").GetString() ?? "",
                    block.GetProperty("input").GetRawText()));
            }
            if (type == "text")
                text.Append(block.GetProperty("text").GetString());
        }
        return new LlmResponse(text.ToString(), null);
    }

    private static object ToApiMessage(Message m) => m switch
    {
        { Role: Role.User } =>
            new { role = "user", content = new object[] { new { type = "text", text = m.Content } } },

        { Role: Role.Assistant, Call: { } c } =>
            new { role = "assistant", content = new object[] {
                new { type = "tool_use", id = c.Id, name = c.Name, input = JsonNode.Parse(c.ArgsJson) } } },

        { Role: Role.Assistant } =>
            new { role = "assistant", content = new object[] { new { type = "text", text = m.Content } } },

        { Role: Role.Tool } =>
            new { role = "user", content = new object[] {
                new { type = "tool_result", tool_use_id = m.ToolCallId ?? "", content = m.Content } } },

        _ => new { role = "user", content = new object[] { new { type = "text", text = m.Content } } }
    };
}
