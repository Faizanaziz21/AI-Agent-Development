using System;
using System.Linq;
using System.Threading.Tasks;

namespace AiAgentDevelopment;

internal static class Program
{
    private const string DemoGoal = "What is 15% of 240, and what time is it?";

    static async Task<int> Main(string[] args)
    {
        bool real = args.Contains("--real", StringComparer.OrdinalIgnoreCase);
        string goal = args.FirstOrDefault(a => !a.StartsWith("--")) ?? DemoGoal;

        ILlmClient llm;
        if (real)
        {
            if (Environment.GetEnvironmentVariable("ANTHROPIC_API_KEY") is null or "")
            {
                Console.WriteLine("--real requires ANTHROPIC_API_KEY. Falling back to the offline mock.\n");
                llm = new MockLlmClient();
            }
            else
            {
                Console.WriteLine("Using the Anthropic API (claude-sonnet-5-5).\n");
                llm = new AnthropicClient();
            }
        }
        else
        {
            Console.WriteLine("Using the offline mock LLM (pass --real with ANTHROPIC_API_KEY for the real model).\n");
            llm = new MockLlmClient();
        }

        var agent = new Agent(llm, new ITool[] { new CalculatorTool(), new TimeTool(), new FileListTool() });
        await agent.RunAsync(goal);
        return 0;
    }
}
