using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading.Tasks;

namespace AiAgentDevelopment;

/// <summary>
/// A deterministic, offline stand-in for a real LLM so the agent can be demonstrated without
/// an API key. It inspects the goal, decides which available tools are needed, requests any it
/// hasn't run yet, and then composes a final answer from the results. It is NOT an LLM — it's a
/// scripted harness for the demo. For real reasoning, use <see cref="AnthropicClient"/>.
/// </summary>
public sealed class MockLlmClient : ILlmClient
{
    public Task<LlmResponse> CompleteAsync(IReadOnlyList<Message> messages, IReadOnlyList<ToolDef> tools)
    {
        string goal = messages.First(m => m.Role == Role.User).Content;
        var toolNames = tools.Select(t => t.Name).ToHashSet(StringComparer.OrdinalIgnoreCase);

        // What results do we already have?
        var results = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var m in messages.Where(m => m.Role == Role.Tool && m.ToolName != null))
            results[m.ToolName!] = m.Content;

        // Decide, in order, which tools this goal needs.
        var plan = new List<(string tool, string args)>();

        if (toolNames.Contains("calculator") && TryBuildExpression(goal, out var expr))
            plan.Add(("calculator", $$"""{"expression":"{{expr}}"}"""));

        if (toolNames.Contains("current_time") &&
            Regex.IsMatch(goal, @"\b(time|date|clock|today|now)\b", RegexOptions.IgnoreCase))
            plan.Add(("current_time", "{}"));

        if (toolNames.Contains("list_files") &&
            Regex.IsMatch(goal, @"\b(file|files|list|directory|folder)\b", RegexOptions.IgnoreCase))
            plan.Add(("list_files", "{}"));

        // Request the first planned tool we haven't run yet.
        foreach (var (tool, args) in plan)
            if (!results.ContainsKey(tool))
                return Task.FromResult(new LlmResponse(null, new ToolCall(Guid.NewGuid().ToString("N")[..8], tool, args)));

        // All needed tools have run — compose a final answer.
        var sb = new StringBuilder();
        if (results.TryGetValue("calculator", out var calc)) sb.Append($"The calculation gives {calc}. ");
        if (results.TryGetValue("current_time", out var time)) sb.Append($"The current time is {time}. ");
        if (results.TryGetValue("list_files", out var files)) sb.Append($"Files: {files}. ");
        if (sb.Length == 0) sb.Append("I don't have a tool for that, but here is the goal restated: " + goal);

        return Task.FromResult(new LlmResponse(sb.ToString().Trim(), null));
    }

    // Turn things like "15% of 240" or "240 * 0.15" into an evaluable expression.
    private static bool TryBuildExpression(string goal, out string expr)
    {
        var pct = Regex.Match(goal, @"(\d+(?:\.\d+)?)\s*%\s*of\s*(\d+(?:\.\d+)?)", RegexOptions.IgnoreCase);
        if (pct.Success) { expr = $"{pct.Groups[2].Value} * {pct.Groups[1].Value} / 100"; return true; }

        var arith = Regex.Match(goal, @"\d+(?:\.\d+)?\s*[-+*/]\s*\d+(?:\.\d+)?(?:\s*[-+*/]\s*\d+(?:\.\d+)?)*");
        if (arith.Success) { expr = arith.Value; return true; }

        expr = ""; return false;
    }
}
