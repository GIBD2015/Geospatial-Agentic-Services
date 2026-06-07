import React, { useState, useEffect } from "react";
import { Key, ShieldAlert, CheckCircle, Trash2, ShieldCheck, Info } from "lucide-react";

interface CredentialsVaultProps {
  isOpen: boolean;
  onClose: () => void;
  onSave: (keys: { OPENAI_API_KEY: string }) => void;
  initialKeys: { OPENAI_API_KEY: string };
}

export const CredentialsVault: React.FC<CredentialsVaultProps> = ({
  isOpen,
  onClose,
  onSave,
  initialKeys,
}) => {
  const [openaiKey, setOpenaiKey] = useState("");

  useEffect(() => {
    setOpenaiKey(initialKeys.OPENAI_API_KEY || "");
  }, [initialKeys, isOpen]);

  if (!isOpen) return null;

  const handleSaveAndClose = () => {
    onSave({
      OPENAI_API_KEY: openaiKey
    });
    onClose();
  };

  const handleClear = () => {
    setOpenaiKey("");
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-neutral-900/60 backdrop-blur-xs select-none">
      <div className="w-full max-w-md bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-850 rounded-xl shadow-xl overflow-hidden p-6 space-y-4">
        {/* Title Header */}
        <div className="flex items-center space-x-3">
          <div className="p-2.5 rounded-lg bg-sky-50 dark:bg-sky-950 text-sky-600 dark:text-sky-400">
            <Key className="w-5 h-5 animate-pulse" />
          </div>
          <div>
            <h3 className="text-sm font-bold text-neutral-800 dark:text-neutral-100">
              API Credentials Vault
            </h3>
            <p className="text-[11px] text-neutral-450 dark:text-neutral-500">
              Provide override keys for execution models.
            </p>
          </div>
        </div>

        {/* Informative Warning */}
        <div className="p-3 rounded-lg bg-neutral-50 dark:bg-neutral-950 border border-neutral-250/50 dark:border-neutral-850/60 text-[10px] text-neutral-500 leading-relaxed flex items-start space-x-2">
          <Info className="w-4 h-4 text-sky-600 dark:text-sky-400 shrink-0 mt-0.5" />
          <span>
            <strong>Secure local caching</strong>: Credentials entered below are cached strictly inside your browser's private local storage. They are never transmitted outside of authorized requests directly targeting your GAS server handler.
          </span>
        </div>

        <div className="space-y-3">
          {/* OpenAI block */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <label className="text-xs font-bold text-neutral-600 dark:text-neutral-400">
                OPENAI_API_KEY Override
              </label>
              {openaiKey ? (
                <span className="text-[9px] text-emerald-600 font-bold bg-emerald-50 dark:bg-emerald-950 px-1 rounded flex items-center">
                  <ShieldCheck className="w-3 h-3 mr-0.5" /> Active Override
                </span>
              ) : (
                <span className="text-[9px] text-neutral-400 italic">No override</span>
              )}
            </div>
            <input
              type="password"
              placeholder="sk-proj-..."
              value={openaiKey}
              onChange={(e) => setOpenaiKey(e.target.value)}
              className="w-full text-xs p-2 bg-neutral-50 dark:bg-neutral-950 border border-neutral-300 dark:border-neutral-700 rounded-lg text-neutral-850 dark:text-neutral-100 focus:outline-none focus:ring-1 focus:ring-sky-500"
            />
          </div>
        </div>

        {/* Buttons Panel */}
        <div className="pt-3 border-t border-neutral-200 dark:border-neutral-800 flex items-center justify-between gap-2.5">
          <button
            onClick={handleClear}
            className="flex items-center space-x-1 py-1.5 px-3 border border-neutral-250 hover:bg-neutral-50 dark:border-neutral-800 dark:hover:bg-neutral-950 text-xs font-semibold text-neutral-600 dark:text-neutral-400 rounded-lg transition-colors"
          >
            <Trash2 className="w-3.5 h-3.5" />
            <span>Clear Inputs</span>
          </button>

          <div className="flex items-center space-x-2">
            <button
              onClick={onClose}
              className="py-1.5 px-3.5 text-xs text-neutral-500 hover:text-neutral-750 font-semibold"
            >
              Cancel
            </button>
            <button
              onClick={handleSaveAndClose}
              className="py-1.5 px-4 bg-sky-600 hover:bg-sky-500 text-white rounded-lg text-xs font-bold shadow-sm transition-colors"
            >
              Save Credentials
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
