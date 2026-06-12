import React, { useEffect, useState } from "react";
import { ExternalLink, Info, Key, ShieldCheck, Trash2 } from "lucide-react";
import { SourceCredentialSpec, SourceCredentials } from "../types";

interface CredentialsVaultProps {
  isOpen: boolean;
  onClose: () => void;
  onSave: (keys: { OPENAI_API_KEY: string }, sourceCredentials: SourceCredentials) => void;
  initialKeys: { OPENAI_API_KEY: string };
  sourceCredentialSpecs: SourceCredentialSpec[];
  initialSourceCredentials: SourceCredentials;
}

const isSecretField = (field: string) => {
  const normalized = field.toLowerCase();
  return normalized.includes("key") || normalized.includes("token") || normalized.includes("secret") || normalized.includes("password");
};

export const CredentialsVault: React.FC<CredentialsVaultProps> = ({
  isOpen,
  onClose,
  onSave,
  initialKeys,
  sourceCredentialSpecs,
  initialSourceCredentials,
}) => {
  const [openaiKey, setOpenaiKey] = useState("");
  const [sourceCredentials, setSourceCredentials] = useState<SourceCredentials>({});

  useEffect(() => {
    setOpenaiKey(initialKeys.OPENAI_API_KEY || "");
    setSourceCredentials(initialSourceCredentials || {});
  }, [initialKeys, initialSourceCredentials, isOpen]);

  if (!isOpen) return null;

  const updateSourceCredential = (sourceId: string, field: string, value: string) => {
    setSourceCredentials((prev) => ({
      ...prev,
      [sourceId]: {
        ...(prev[sourceId] || {}),
        [field]: value,
      },
    }));
  };

  const handleSaveAndClose = () => {
    onSave({ OPENAI_API_KEY: openaiKey }, sourceCredentials);
    onClose();
  };

  const handleClear = () => {
    setOpenaiKey("");
    setSourceCredentials({});
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-neutral-900/60 backdrop-blur-xs select-none">
      <div className="flex max-h-[88vh] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-neutral-200 bg-white shadow-xl dark:border-neutral-850 dark:bg-neutral-900">
        <div className="space-y-4 border-b border-neutral-200 p-6 pb-4 dark:border-neutral-800">
          <div className="flex items-center space-x-3">
            <div className="rounded-lg bg-sky-50 p-2.5 text-sky-600 dark:bg-sky-950 dark:text-sky-400">
              <Key className="h-5 w-5 animate-pulse" />
            </div>
            <div>
              <h3 className="text-sm font-bold text-neutral-800 dark:text-neutral-100">
                API Credentials Vault
              </h3>
              <p className="text-[11px] text-neutral-450 dark:text-neutral-500">
                Store local model and data-source keys for upcoming GAS requests.
              </p>
            </div>
          </div>

          <div className="flex items-start space-x-2 rounded-lg border border-neutral-250/50 bg-neutral-50 p-3 text-[10px] leading-relaxed text-neutral-500 dark:border-neutral-850/60 dark:bg-neutral-950">
            <Info className="mt-0.5 h-4 w-4 shrink-0 text-sky-600 dark:text-sky-400" />
            <span>
              <strong>Local browser storage</strong>: Credentials entered below are cached in this browser and sent only with authorized requests to the selected GAS server handler.
            </span>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-6 space-y-5">
          <section className="space-y-3">
            <div>
              <h4 className="text-[11px] font-bold uppercase text-neutral-500 dark:text-neutral-400">Model Credentials</h4>
              <p className="mt-0.5 text-xs text-neutral-500">Used by model-backed agents unless a node provides its own override.</p>
            </div>

            <div className="space-y-1.5">
              <div className="flex items-center justify-between gap-3">
                <label className="text-xs font-bold text-neutral-600 dark:text-neutral-400">
                  OPENAI_API_KEY
                </label>
                {openaiKey ? (
                  <span className="flex items-center rounded bg-emerald-50 px-1 text-[9px] font-bold text-emerald-600 dark:bg-emerald-950">
                    <ShieldCheck className="mr-0.5 h-3 w-3" /> Configured
                  </span>
                ) : (
                  <span className="text-[9px] italic text-neutral-400">No override</span>
                )}
              </div>
              <input
                type="password"
                placeholder="sk-proj-..."
                value={openaiKey}
                onChange={(e) => setOpenaiKey(e.target.value)}
                className="w-full rounded-lg border border-neutral-300 bg-neutral-50 p-2 text-xs text-neutral-850 focus:outline-none focus:ring-1 focus:ring-sky-500 dark:border-neutral-700 dark:bg-neutral-950 dark:text-neutral-100"
              />
            </div>
          </section>

          <section className="space-y-3 border-t border-neutral-200 pt-4 dark:border-neutral-800">
            <div>
              <h4 className="text-[11px] font-bold uppercase text-neutral-500 dark:text-neutral-400">Data Source Credentials</h4>
              <p className="mt-0.5 text-xs text-neutral-500">Generated from connected agents that declare source-specific API requirements.</p>
            </div>

            {sourceCredentialSpecs.length === 0 ? (
              <div className="rounded-lg border border-dashed border-neutral-250 bg-neutral-50 p-3 text-xs leading-relaxed text-neutral-500 dark:border-neutral-800 dark:bg-neutral-950/40">
                No loaded agents currently declare data-source API keys. Select or view an agent with source credential requirements to show its fields here.
              </div>
            ) : (
              <div className="space-y-3">
                {sourceCredentialSpecs.map((spec) => {
                  const savedFields = sourceCredentials[spec.sourceId] || {};
                  const completedFields = spec.fields.filter((field) => Boolean(savedFields[field]?.trim())).length;
                  return (
                    <div key={spec.sourceId} className="rounded-lg border border-neutral-200 bg-neutral-50/70 p-3 dark:border-neutral-800 dark:bg-neutral-950/40">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <h5 className="text-xs font-bold text-neutral-800 dark:text-neutral-200">{spec.name}</h5>
                          <p className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-neutral-500">{spec.description}</p>
                        </div>
                        <span className={`shrink-0 rounded px-1.5 py-0.5 text-[9px] font-bold ${completedFields === spec.fields.length ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950" : "bg-amber-50 text-amber-700 dark:bg-amber-950"}`}>
                          {completedFields}/{spec.fields.length}
                        </span>
                      </div>

                      <div className="mt-3 grid gap-2 sm:grid-cols-2">
                        {spec.fields.map((field) => (
                          <div key={field} className="space-y-1">
                            <label className="text-[9px] font-bold uppercase text-neutral-500">{field}</label>
                            <input
                              type={isSecretField(field) ? "password" : "text"}
                              placeholder={field === "email" ? "you@example.com" : field}
                              value={savedFields[field] || ""}
                              onChange={(e) => updateSourceCredential(spec.sourceId, field, e.target.value)}
                              className="w-full rounded-md border border-neutral-300 bg-white p-2 text-xs text-neutral-800 focus:outline-none focus:ring-1 focus:ring-sky-500 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-100"
                            />
                          </div>
                        ))}
                      </div>

                      {spec.registrationUrl && (
                        <a
                          href={spec.registrationUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="mt-2 inline-flex items-center gap-1 text-[10px] font-semibold text-sky-700 hover:text-sky-600 hover:underline"
                        >
                          <ExternalLink className="h-3 w-3" />
                          <span>Get credentials</span>
                        </a>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </section>
        </div>

        <div className="flex items-center justify-between gap-2.5 border-t border-neutral-200 p-4 dark:border-neutral-800">
          <button
            onClick={handleClear}
            className="flex items-center space-x-1 rounded-lg border border-neutral-250 px-3 py-1.5 text-xs font-semibold text-neutral-600 transition-colors hover:bg-neutral-50 dark:border-neutral-800 dark:text-neutral-400 dark:hover:bg-neutral-950"
          >
            <Trash2 className="h-3.5 w-3.5" />
            <span>Clear Inputs</span>
          </button>

          <div className="flex items-center space-x-2">
            <button
              onClick={onClose}
              className="px-3.5 py-1.5 text-xs font-semibold text-neutral-500 hover:text-neutral-750"
            >
              Cancel
            </button>
            <button
              onClick={handleSaveAndClose}
              className="rounded-lg bg-sky-600 px-4 py-1.5 text-xs font-bold text-white shadow-sm transition-colors hover:bg-sky-500"
            >
              Save Credentials
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
