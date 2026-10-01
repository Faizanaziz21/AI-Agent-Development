using System;
using System.Data;
using System.IO;
using System.Linq;
using System.Text.Json;

namespace AiAgentDevelopment;

/// <summary>A capability the agent can invoke. Execute receives the raw JSON args from the model.</summary>
public interface ITool
{
    string Name { get; }
    string Description { get; }
    string InputSchemaJson { get; }
    string Execute(string argsJson);
}

/// <summary>Evaluates an arithmetic expression (e.g. "240 * 0.15").</summary>
public sealed class CalculatorTool : ITool
{
    public string Name => "calculator";
    public string Description => "Evaluate a basic arithmetic expression with + - * / and parentheses.";
    public string InputSchemaJson =>
        """{"type":"object","properties":{"expression":{"type":"string","description":"e.g. 240 * 0.15"}},"required":["expression"]}""";

    public string Execute(string argsJson)
    {
        try
        {
            var expr = JsonDocument.Parse(argsJson).RootElement.GetProperty("expression").GetString() ?? "";
            var result = new DataTable().Compute(expr, null);
            return Convert.ToString(result, System.Globalization.CultureInfo.InvariantCulture) ?? "";
        }
        catch (Exception ex) { return $"error: {ex.Message}"; }
    }
}

/// <summary>Returns the current local date and time.</summary>
public sealed class TimeTool : ITool
{
    public string Name => "current_time";
    public string Description => "Get the current local date and time.";
    public string InputSchemaJson => """{"type":"object","properties":{}}""";

    public string Execute(string argsJson) => DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss");
}

/// <summary>Lists the files in a directory (defaults to the current directory).</summary>
public sealed class FileListTool : ITool
{
    public string Name => "list_files";
    public string Description => "List the files in a directory.";
    public string InputSchemaJson =>
        """{"type":"object","properties":{"path":{"type":"string","description":"directory path; defaults to '.'"}}}""";

    public string Execute(string argsJson)
    {
        try
        {
            string path = ".";
            var root = JsonDocument.Parse(argsJson).RootElement;
            if (root.TryGetProperty("path", out var p) && p.GetString() is { Length: > 0 } s) path = s;
            var files = Directory.EnumerateFiles(path).Select(Path.GetFileName).Take(100);
            return string.Join(", ", files);
        }
        catch (Exception ex) { return $"error: {ex.Message}"; }
    }
}
