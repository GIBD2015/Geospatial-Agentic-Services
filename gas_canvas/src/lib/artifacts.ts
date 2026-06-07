import { TaskArtifact, TaskResult } from "../types";

export const getArtifactFormat = (artifact: Partial<TaskArtifact>) =>
  String(artifact.format || artifact.mime_type || "").toLowerCase();

export const getArtifactFilenameFromUrl = (url = "") => {
  try {
    const pathname = new URL(url, window.location.href).pathname;
    return decodeURIComponent(pathname.split("/").pop() || "");
  } catch {
    return decodeURIComponent(url.split("?")[0].split("/").pop() || "");
  }
};

export const getArtifactFilename = (artifact: Partial<TaskArtifact>, fallback = "artifact") =>
  artifact.filename ||
  artifact.original_filename ||
  getArtifactFilenameFromUrl(artifact.url) ||
  artifact.name ||
  fallback;

export const getArtifactSemanticName = (artifact: Partial<TaskArtifact>, fallback = "Artifact") =>
  artifact.name || artifact.label || artifact.role || fallback;

export const getArtifactPreviewTitle = (artifact: Partial<TaskArtifact>, fallback = "Artifact Preview") =>
  getArtifactSemanticName(artifact, getArtifactFilename(artifact, fallback));

export const getArtifactDescription = (artifact: Partial<TaskArtifact>) =>
  artifact.description || "";

export const getArtifactHoverText = (artifact: Partial<TaskArtifact>) => {
  const description = getArtifactDescription(artifact);
  const semanticName = getArtifactSemanticName(artifact, "");
  const filename = getArtifactFilename(artifact, "");
  const format = getArtifactFormat(artifact);
  const lines = [
    description,
    semanticName && semanticName !== filename ? `Name: ${semanticName}` : "",
    filename ? `Filename: ${filename}` : "",
    format ? `Format: ${format.toUpperCase()}` : "",
    artifact.role ? `Role: ${artifact.role}` : ""
  ].filter(Boolean);
  return lines.join("\n") || filename || semanticName || "Artifact";
};

export const getArtifactSelectionKey = (artifact: Partial<TaskArtifact>, index = 0) =>
  getArtifactFilename(artifact, artifact.name || `artifact_${index + 1}`);

export const artifactMatchesSelection = (artifact: Partial<TaskArtifact>, selected: string[], index = 0) => {
  if (selected.length === 0) return true;
  const candidates = [
    getArtifactSelectionKey(artifact, index),
    artifact.name,
    artifact.filename,
    artifact.original_filename,
    artifact.url
  ].filter(Boolean);
  return candidates.some((candidate) => selected.includes(String(candidate)));
};

export const normalizeTaskArtifacts = (artifacts: TaskArtifact[] = []) =>
  artifacts
    .filter((artifact) => artifact?.url)
    .map((artifact, index) => {
      const filename = getArtifactFilename(artifact, `artifact_${index + 1}.${artifact.format || "file"}`);
      return {
        ...artifact,
        filename,
        name: artifact.name || artifact.label || filename,
        format: artifact.format || filename.split(".").pop() || "file"
      };
    });

export const normalizeTaskResultArtifacts = (result: TaskResult): TaskResult => ({
  ...result,
  outputs: result.outputs
    ? {
        ...result.outputs,
        artifacts: normalizeTaskArtifacts(result.outputs.artifacts || [])
      }
    : result.outputs
});
