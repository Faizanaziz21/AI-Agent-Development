# AI Agent Development — tool-using agent (C# / .NET)

A minimal but real **tool-using AI agent** in C#/.NET. Give it a goal; it loops — ask the model,
and when the model requests a tool, run it and feed the result back — until it produces a final
answer. This is the core **ReAct / tool-calling** pattern every "AI agent" is built on, kept small
enough to read in one sitting.

```
▶ goal: What is 15% of 240, and what time is it?

  [1] tool  → calculator({"expression":"240 * 15 / 100"})
        result: 36
  [2] tool  → current_time({})
        result: 2026-10-02 02:32:12

✔ answer: The calculation gives 36. The current time is 2026-10-02 02:32:12.
```

## Design

- **Pluggable LLM** (`ILlmClient`) — the agent loop depends only on this interface:
  - `AnthropicClient` — the **real** Anthropic Messages API with native tool use (`tool_use` /
    `tool_result` blocks); reads `ANTHROPIC_API_KEY`, defaults to `claude-sonnet-5-5`.
  - `MockLlmClient` — a **deterministic, offline** stand-in so the agent runs with no API key
    (it plans which tools a goal needs, runs them, and composes the answer). Clearly a harness,
    not an LLM.
- **Tools** (`ITool`) — each exposes a name, description and JSON input-schema the model sees:
  `calculator`, `current_time`, `list_files`. Adding a tool = one class.
- **Agent loop** (`Agent.cs`) — ask → (tool call? run it, append `tool_use` + `tool_result`, repeat)
  → final text, with a max-steps guard.

The message model mirrors the real API, so the mock and the live model are fully interchangeable.

## Run

```
dotnet run                                   # offline mock (default)
dotnet run -- "what files are in this folder?"   # your own goal
setx ANTHROPIC_API_KEY sk-ant-...            # then, for the real model:
dotnet run -- --real "plan a 3-step task and use tools"
```

## Build

```
dotnet build -c Release
```

Requires the .NET SDK. No external NuGet packages — the Anthropic call uses `HttpClient` and
`System.Text.Json` directly.
