import { getArtifactDescription, getArtifactFilename, getArtifactSemanticName } from "./artifacts";

export async function determineRelevantArtifacts(
  apiKey: string,
  sourceInstruction: string,
  targetInstruction: string,
  artifacts: { name?: string; filename?: string; original_filename?: string; label?: string; role?: string; description?: string; url?: string; format?: string; mime_type?: string }[]
): Promise<string[]> {
  const routeItems = artifacts.map((artifact, index) => {
    const filename = getArtifactFilename(artifact, `artifact_${index + 1}`);
    const semanticName = getArtifactSemanticName(artifact, filename);
    return {
      id: filename,
      semanticName,
      filename,
      role: artifact.role || "",
      format: artifact.format || artifact.mime_type || "unknown",
      description: getArtifactDescription(artifact) || "none",
      url: artifact.url || ""
    };
  });

  if (!apiKey) return routeItems.map((item) => item.id);
  if (routeItems.length <= 1) return routeItems.map((item) => item.id);

  try {
    const payload = {
      model: "gpt-4o-mini",
      messages: [
        {
          role: "system",
          content:
            "You are an intelligent data router for a geospatial agentic pipeline. Select which output datasets from a source agent should be passed as inputs to a target agent. Respond with a JSON object containing 'selected_artifacts', an array of exact artifact IDs from the provided list.",
        },
        {
          role: "user",
          content: `Source Agent Instruction: ${sourceInstruction}\n\nTarget Agent Instruction: ${targetInstruction}\n\nAvailable Output Artifacts from Source Agent:\n${artifacts
            .map((artifact, index) => {
              const item = routeItems[index];
              return `- ID: ${item.id}\n  Name: ${item.semanticName}\n  Filename: ${item.filename}\n  Role: ${item.role || "none"}\n  Format: ${item.format}\n  Description: ${item.description}\n  URL: ${item.url || "none"}`;
            })
            .join("\n")}\n\nWhich of these artifacts should be passed to the target agent? Return ONLY a JSON object with 'selected_artifacts'. Use exact artifact IDs. If all are needed, include all IDs. If none are relevant, return an empty array.`,
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

    // Validate exact route IDs, while accepting older LLM/name responses for compatibility.
    const validSelected = selected
      .map((selectedName: string) => {
        const selectedText = String(selectedName);
        const match = routeItems.find((item) =>
          item.id === selectedText ||
          item.semanticName === selectedText ||
          item.filename === selectedText ||
          item.url === selectedText
        );
        return match?.id || "";
      })
      .filter(Boolean);
    return validSelected;
  } catch (err) {
    console.error("Error calling LLM for routing:", err);
  }

  // fallback
  return routeItems.map((item) => item.id);
}
