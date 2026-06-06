import React, { useState, useEffect } from "react";
import { X, ExternalLink, ShieldCheck, Box, Settings, Cpu, Tag, Server, CheckCircle2, ChevronDown, Save } from "lucide-react";

interface AgentDescribePanelProps {
  agentInfo: any;
  onClose: () => void;
  width: number;
}

export const AgentDescribePanel: React.FC<AgentDescribePanelProps> = ({ agentInfo, onClose, width }) => {
  const [sourceProvider, setSourceProvider] = useState<string>("US_Census_demography");
  const [sourceKey, setSourceKey] = useState<string>("");
  const [sourceEmail, setSourceEmail] = useState<string>("");

  useEffect(() => {
    // Load source credentials from local storage
    const saved = localStorage.getItem("gas_source_credentials");
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        if (parsed[sourceProvider]) {
          setSourceKey(parsed[sourceProvider].key || "");
          setSourceEmail(parsed[sourceProvider].email || "");
        } else {
          setSourceKey("");
          setSourceEmail("");
        }
      } catch (e) {}
    } else {
      setSourceKey("");
      setSourceEmail("");
    }
  }, [sourceProvider, agentInfo]);

  const saveSourceCredentials = () => {
    const saved = localStorage.getItem("gas_source_credentials");
    let parsed: any = {};
    if (saved) {
      try {
        parsed = JSON.parse(saved);
      } catch (e) {}
    }

    parsed[sourceProvider] = { key: sourceKey };
    if (sourceProvider === "EPA_AQS") {
      parsed[sourceProvider].email = sourceEmail;
    }

    localStorage.setItem("gas_source_credentials", JSON.stringify(parsed));
    // Optional: show a quick Toast here if there was a local state for it, but updating localStorage is enough.
  };

  if (!agentInfo || !agentInfo.profile) {
    return (
      <div style={{ width: `${width}px` }} className="border-l border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-900 flex flex-col items-center justify-center p-6 shrink-0 h-full text-center">
        <Cpu className="w-10 h-10 text-neutral-300 dark:text-neutral-700 mb-2 animate-pulse" />
        <h4 className="text-sm font-semibold text-neutral-500">Loading Agent Profile...</h4>
      </div>
    );
  }

  const { profile, keywords, skills, execute_task, governance, provenance_and_reproducibility } = agentInfo;

  return (
    <div style={{ width: `${width}px` }} className="border-l border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-900 flex flex-col h-full shrink-0 shadow-xl overflow-hidden">
      {/* HEADER SECTION */}
      <div className="p-4 border-b border-neutral-100 dark:border-neutral-800 flex items-start justify-between bg-neutral-50/50 dark:bg-neutral-950/40">
        <div className="flex-1 overflow-hidden pr-2">
          <div className="flex items-center space-x-1.5 mb-1 text-[10px] text-sky-600 font-bold font-mono uppercase bg-sky-50 dark:bg-sky-950/40 px-1.5 py-0.5 rounded w-fit">
            <Box className="w-3.5 h-3.5" />
            <span>v{profile.version || "1.0.0"}</span>
          </div>
          <h4 className="text-sm font-bold text-neutral-800 dark:text-neutral-200 truncate leading-tight">
            {profile.name}
          </h4>
          <div className="mt-1 space-y-0.5">
            <p className="text-[11px] text-neutral-500 font-mono truncate">
              {profile.provider?.name || "Geoinformation and Big Data Research Lab (GIBD)"}
            </p>
          </div>
        </div>
        <button
          onClick={onClose}
          className="p-1 hover:bg-neutral-200 rounded dark:hover:bg-neutral-800 text-neutral-500 shrink-0"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-5">
        {/* OVERVIEW */}
        <section className="space-y-1.5">
          <h5 className="text-[11px] font-bold text-neutral-400 uppercase tracking-wider flex items-center space-x-1 border-b border-neutral-100 dark:border-neutral-800 pb-1">
            <Settings className="w-3 h-3" />
            <span>Description</span>
          </h5>
          <p className="text-xs text-neutral-600 dark:text-neutral-300 leading-relaxed">
            {profile.description}
          </p>
          <div className="mt-2.5 flex flex-wrap gap-1.5">
            {keywords?.map((kw: string, i: number) => (
              <span key={i} className="text-[9px] bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300 px-1.5 py-0.5 rounded flex items-center">
                <Tag className="w-2.5 h-2.5 mr-1 text-neutral-400" />
                {kw}
              </span>
            ))}
          </div>
        </section>

        {/* PROFILE/PROVIDER SECTION */}
        <section className="p-3 bg-neutral-50 dark:bg-neutral-950/50 rounded-lg border border-neutral-200/60 dark:border-neutral-800 space-y-2">
          <div className="flex items-center space-x-1.5 text-neutral-700 dark:text-neutral-200">
            <Server className="w-4 h-4" />
            <span className="text-xs font-bold">Provider Information</span>
          </div>
          <div className="space-y-1.5">
            <div className="text-[11px] font-mono text-neutral-500">
              <strong className="text-neutral-600 dark:text-neutral-300">Name:</strong> {profile.provider?.name || "Unknown"}
            </div>
            {profile.provider?.website && (
              <div className="text-[11px] font-mono">
                <a href={profile.provider.website} target="_blank" rel="noreferrer" className="text-sky-600 hover:underline flex items-center space-x-1">
                  <span>Visit Website</span>
                  <ExternalLink className="w-3 h-3" />
                </a>
              </div>
            )}
            {profile.default_model && (
              <div className="text-[11px] font-mono text-neutral-500 mt-1 flex items-center space-x-1">
                <strong className="text-neutral-600 dark:text-neutral-300">Default Model:</strong>
                <span className="bg-neutral-200 dark:bg-neutral-800 px-1 py-0.5 rounded shrink-0">{profile.default_model}</span>
              </div>
            )}
          </div>
        </section>

        {/* SKILLS */}
        {skills && skills.length > 0 && (
          <section className="space-y-2">
            <h5 className="text-[11px] font-bold text-neutral-400 uppercase tracking-wider flex items-center space-x-1 border-b border-neutral-100 dark:border-neutral-800 pb-1">
              <Cpu className="w-3 h-3" />
              <span>Available Capabilities ({skills.length})</span>
            </h5>
            <div className="space-y-2">
              {skills.map((skill: any, idx: number) => (
                <div key={idx} className="p-2 border border-neutral-200 dark:border-neutral-800 rounded bg-white dark:bg-neutral-900/60">
                  <div className="font-bold text-[11px] text-neutral-800 dark:text-neutral-200">
                    {skill.name}
                  </div>
                  <p className="text-[10px] text-neutral-500 mt-1 leading-normal">
                    {skill.description}
                  </p>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* SOURCE CREDENTIALS (Only for Geospatial Data Retrieval Agent) */}
        {(profile.name === "Geospatial Data Retrieval Agent" || agentInfo.id === "geospatial_data_retrieval_agent" || agentInfo.agent_id === "geospatial_data_retrieval_agent" || agentInfo.agentId === "geospatial_data_retrieval_agent") && (
          <section className="space-y-2">
            <h5 className="text-[11px] font-bold text-neutral-400 uppercase tracking-wider flex items-center space-x-1 border-b border-neutral-100 dark:border-neutral-800 pb-1">
              <Server className="w-3 h-3" />
              <span>Source Credentials</span>
            </h5>
            <div className="p-3 bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded flex flex-col space-y-2">
              <label className="text-[10px] font-bold text-neutral-600 dark:text-neutral-400">
                Select Source Provider
              </label>
              <div className="relative">
                <select 
                  value={sourceProvider}
                  onChange={(e) => setSourceProvider(e.target.value)}
                  className="w-full appearance-none bg-neutral-100 dark:bg-neutral-800 text-neutral-800 dark:text-neutral-200 text-xs px-3 py-2 rounded border border-neutral-200 dark:border-neutral-700 outline-none focus:border-sky-500 pr-8 transition-colors"
                >
                  <option value="US_Census_demography">US Census Bureau Demography</option>
                  <option value="OpenTopography">OpenTopography</option>
                  <option value="OpenWeather">OpenWeather</option>
                  <option value="EPA_AQS">EPA Air Quality System (AQS)</option>
                </select>
                <ChevronDown className="w-3.5 h-3.5 text-neutral-500 absolute right-2.5 top-2.5 pointer-events-none" />
              </div>
              
              {sourceProvider === "EPA_AQS" && (
                <div className="space-y-1 mt-1">
                  <label className="text-[10px] font-bold text-neutral-600 dark:text-neutral-400">
                    Email
                  </label>
                  <input
                    type="email"
                    value={sourceEmail}
                    onChange={(e) => setSourceEmail(e.target.value)}
                    placeholder="Registered Email"
                    className="w-full text-xs px-3 py-1.5 rounded border border-neutral-200 dark:border-neutral-800 bg-neutral-50 dark:bg-neutral-950 text-neutral-800 dark:text-neutral-200 outline-none focus:ring-1 focus:ring-sky-500 transition-shadow transition-colors"
                  />
                </div>
              )}
              
              <div className="space-y-1 mt-1">
                <label className="text-[10px] font-bold text-neutral-600 dark:text-neutral-400">
                  Key
                </label>
                <input
                  type="text"
                  value={sourceKey}
                  onChange={(e) => setSourceKey(e.target.value)}
                  placeholder="Key"
                  autoComplete="off"
                  className="w-full text-xs px-3 py-1.5 rounded border border-neutral-200 dark:border-neutral-800 bg-neutral-50 dark:bg-neutral-950 text-neutral-800 dark:text-neutral-200 outline-none focus:ring-1 focus:ring-sky-500 transition-shadow transition-colors"
                />
              </div>

              <div className="flex justify-end pt-2">
                <button 
                  onClick={saveSourceCredentials}
                  className="flex items-center space-x-1.5 px-3 py-1.5 bg-neutral-800 dark:bg-neutral-200 text-white dark:text-neutral-900 text-xs font-bold rounded shadow hover:bg-neutral-700 dark:hover:bg-white transition-colors"
                >
                  <Save className="w-3.5 h-3.5" />
                  <span>Save Config</span>
                </button>
              </div>
              
            </div>
          </section>
        )}

        {/* GOVERNANCE & PROVENANCE */}
        <section className="space-y-2">
          <h5 className="text-[11px] font-bold text-neutral-400 uppercase tracking-wider flex items-center space-x-1 border-b border-neutral-100 dark:border-neutral-800 pb-1">
            <ShieldCheck className="w-3 h-3" />
            <span>Governance & Provenance</span>
          </h5>
          <div className="space-y-2">
            {governance?.operation_policy?.description && (
              <div className="text-[10px] flex items-start space-x-1.5 p-2 bg-rose-50/50 dark:bg-rose-950/20 text-rose-800 dark:text-rose-400 rounded">
                <AlertTriangle className="w-3 h-3 shrink-0 mt-0.5" />
                <span className="leading-relaxed"><strong>Policy:</strong> {governance.operation_policy.description}</span>
              </div>
            )}
            
            {(provenance_and_reproducibility?.reproducibility?.supported || provenance_and_reproducibility?.provenance?.supported) && (
              <div className="text-[10px] flex items-start space-x-1.5 p-2 bg-emerald-50/50 dark:bg-emerald-950/20 text-emerald-800 dark:text-emerald-400 rounded">
                <CheckCircle2 className="w-3 h-3 shrink-0 mt-0.5" />
                <span className="leading-relaxed"><strong>Reproducibility:</strong> {provenance_and_reproducibility.reproducibility?.description || provenance_and_reproducibility.provenance?.description}</span>
              </div>
            )}
          </div>
        </section>

      </div>
    </div>
  );
};

// Required Lucide local import for AlertTriangle 
import { AlertTriangle } from "lucide-react";
