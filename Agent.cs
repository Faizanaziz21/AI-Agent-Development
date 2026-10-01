using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;

namespace AiAgentDevelopment;

/// <summary>
/// A minimal tool-using agent. Given a goal, it loops: ask the model, and if the model
/// requests a tool, run it and feed the result back; otherwise return the final answer.
/// This is the core ReAct / tool-calling pattern every "AI agent" is built on.
/// </summary>
public sealed class Agent
{
    private readonly ILlmClient _llm;
    private readonly Dictionary<string, ITool> _tools;
    private readonly IReadOnlyList<ToolDef> _defs;
    private readonly int _maxSteps;

    public Agent(ILlmClient llm, IEnumerable<ITool> tools, int maxSteps = 8)
    {
        _llm = llm;
        _tools = tools.ToDictionary(t => t.Name, StringComparer.OrdinalIgnoreCase);
        _defs = _tools.Values.Select(t => new ToolDef(t.Name, t.Description, t.InputSchemaJson)).ToList();
        _maxSteps = maxSteps;
    }

    public async Task<string> RunAsync(string goal, Action<string>? log = null)
    {
        void Log(string s) => (log ?? Console.WriteLine)(s);

        var messages = new List<Message> { new(Role.User, goal) };
        Log($"▶ goal: {goal}\n");

        for (int step = 1; step <= _maxSteps; step++)
        {
            var resp = await _llm.CompleteAsync(messages, _defs);

            if (resp.Call is { } call)
            {
                Log($"  [{step}] tool  → {call.Name}({call.ArgsJson})");

                string result = _tools.TryGetValue(call.Name, out var tool)
                    ? tool.Execute(call.ArgsJson)
                    : $"error: unknown tool '{call.Name}'";

                Log($"        result: {result}");

                // Record the assistant's tool request and the tool's result, then loop.
                messages.Add(new Message(Role.Assistant, "", Call: call));
                messages.Add(new Message(Role.Tool, result, ToolName: call.Name, ToolCallId: call.Id));
                continue;
            }

            Log($"\n✔ answer: {resp.Text}");
            return resp.Text ?? "";
        }

        Log("\n✗ stopped: reached max steps without a final answer");
        return "(no final answer)";
    }
}
