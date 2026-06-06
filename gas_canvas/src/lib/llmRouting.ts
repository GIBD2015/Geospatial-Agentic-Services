export async function determineRelevantArtifacts(
  apiKey: string,
  sourceInstruction: string,
  targetInstruction: string,
  artifacts: { name?: string; description?: string; url?: string; format?: string }[]
): Promise<string[]> {
  if (!apiKey) return artifacts.map((a) => a.name || "");
  if (artifacts.length <= 1) return artifacts.map((a) => a.name || "");

  try {
    const payload = {
      model: "gpt-4o-mini",
      messages: [
        {
          role: "system",
          content:
            "You are an intelligent data router for a geospatial agentic pipeline. Your job is to select which output datasets from a source agent should be passed as inputs to a target agent. Respond with a JSON object containing a property 'selected_artifacts' which is an array of strings (exact artifact names).",
        },
        {
          role: "user",
          content: `Source Agent Instruction: ${sourceInstruction}\n\nTarget Agent Instruction: ${targetInstruction}\n\nAvailable Output Artifacts from Source Agent:\n${artifacts
            .map(
              (a) =>
                `- ${a.name} (Format: ${a.format || "unknown"}, Desc: ${
                  a.description || "none"
                })`
            )
            .join("\n")}\n\nWhich of these artifacts should be passed to the target agent? Return ONLY a JSON object with 'selected_artifacts'. If all are needed, include all names. If none, return empty array.`,
        },
      ],
      response_format: { type: "json_object" },
    };

    const res = await fetch("https://api.openai.com/v1/chat/completions", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${apiKey}`,
      },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      console.error(
        "OpenAI API error routing artifacts:",
        await res.text()
      );
      return artifacts.map((a) => a.name || "");
    }

    const data = await res.json();
    const content = JSON.parse(data.choices[0].message.content);
    const selected = content.selected_artifacts || [];

    // Validate that selected exist in artifacts
    const validSelected = selected.filter((name: string) =>
      artifacts.some((a) => a.name === name)
    );
    return validSelected;
  } catch (err) {
    console.error("Error calling LLM for routing:", err);
  }

  // fallback
  return artifacts.map((a) => a.name || "");
}
