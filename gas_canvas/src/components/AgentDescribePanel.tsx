import React from "react";
import {
  Box,
  CheckCircle2,
  Cpu,
  Database,
  ExternalLink,
  FileJson,
  KeyRound,
  Layers,
  Server,
  ShieldCheck,
  Tag,
  X,
  XCircle,
} from "lucide-react";

interface AgentDescribePanelProps {
  agentInfo: any;
  onClose: () => void;
  width: number;
}

type FactValue = React.ReactNode | string | number | boolean | null | undefined | any[];

const isYes = (value: any) => {
  if (value === true) return true;
  if (typeof value === "string") return value.toLowerCase() === "true";
  return false;
};

const displayValue = (value: any): string => {
  if (value === true) return "Yes";
  if (value === false) return "No";
  if (value === undefined || value === null || value === "") return "";
  if (Array.isArray(value)) return value.map(displayValue).filter(Boolean).join(", ");
  if (typeof value === "object") {
    if (value.supported !== undefined && value.description) {
      return `${isYes(value.supported) ? "Supported. " : "Not supported. "}${value.description}`;
    }
    if (value.description) return value.description;
    if (value.supported !== undefined) return isYes(value.supported) ? "Supported" : "Not supported";
    return JSON.stringify(value);
  }
  return String(value);
};

const CodeList: React.FC<{ values: any[] }> = ({ values }) => (
  <div className="flex flex-wrap gap-1">
    {values.map((value, index) => (
      <code key={index} className="rounded bg-neutral-100 px-1.5 py-0.5 text-[11px] text-neutral-700">
        {displayValue(value)}
      </code>
    ))}
  </div>
);

const ProviderLink: React.FC<{ name?: string; website?: string }> = ({ name, website }) => {
  const label = name || website || "";
  if (!label) return null;
  if (!website) return <>{label}</>;
  return (
    <a
      href={website}
      target="_blank"
      rel="noreferrer"
      className="inline-flex min-w-0 items-center gap-1 text-sky-600 hover:underline"
      title={website}
    >
      <span className="truncate">{label}</span>
      <ExternalLink className="h-3 w-3 shrink-0" />
    </a>
  );
};

const FactValueView: React.FC<{ value: FactValue }> = ({ value }) => {
  if (value === undefined || value === null || value === "") return null;
  if (Array.isArray(value)) return <CodeList values={value} />;
  if (React.isValidElement(value)) return value;
  if (typeof value === "object") return <>{displayValue(value)}</>;
  return <>{displayValue(value)}</>;
};

const FactsSection: React.FC<{
  title: string;
  icon?: React.ReactNode;
  rows: Array<[string, FactValue]>;
  compact?: boolean;
}> = ({ title, icon, rows, compact = false }) => {
  const filtered = rows.filter(([, value]) => {
    if (value === undefined || value === null || value === "") return false;
    if (Array.isArray(value) && value.length === 0) return false;
    return true;
  });

  if (!filtered.length) return null;

  return (
    <section className={`${compact ? "space-y-2" : "space-y-2.5"}`}>
      <h5 className="flex items-center gap-1.5 border-b border-neutral-100 pb-1 text-[11px] font-bold uppercase tracking-wide text-neutral-500">
        {icon}
        <span>{title}</span>
      </h5>
      <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-[12px] leading-snug">
        {filtered.map(([label, value]) => (
          <React.Fragment key={label}>
            <dt className="font-semibold text-neutral-800">{label}</dt>
            <dd className="min-w-0 break-words text-neutral-600">
              <FactValueView value={value} />
            </dd>
          </React.Fragment>
        ))}
      </dl>
    </section>
  );
};

const TagsSection: React.FC<{ title: string; values?: string[] }> = ({ title, values }) => {
  if (!values?.length) return null;
  return (
    <section className="space-y-2">
      <h5 className="flex items-center gap-1.5 border-b border-neutral-100 pb-1 text-[11px] font-bold uppercase tracking-wide text-neutral-500">
        <Tag className="h-3 w-3" />
        <span>{title}</span>
      </h5>
      <div className="flex flex-wrap gap-1.5">
        {values.map((value) => (
          <span key={value} className="rounded-lg bg-neutral-100 px-2 py-1 text-[11px] font-medium text-neutral-700">
            {value}
          </span>
        ))}
      </div>
    </section>
  );
};

const CapabilityBadges: React.FC<{ pr: any }> = ({ pr }) => {
  const items = [
    { key: "provenance", label: "Provenance" },
    { key: "reproducibility", label: "Reproducibility" },
    { key: "validation", label: "Validation" },
  ].filter((item) => pr?.[item.key]);

  if (!items.length) return null;

  return (
    <section className="space-y-2">
      <h5 className="flex items-center gap-1.5 border-b border-neutral-100 pb-1 text-[11px] font-bold uppercase tracking-wide text-neutral-500">
        <ShieldCheck className="h-3 w-3" />
        <span>Capabilities</span>
      </h5>
      <div className="flex flex-wrap gap-2">
        {items.map((item) => {
          const info = pr[item.key];
          const supported = isYes(info.supported);
          return (
            <span
              key={item.key}
              title={info.description || (supported ? "Supported" : "Not supported")}
              className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] font-semibold ${
                supported
                  ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                  : "border-rose-200 bg-rose-50 text-rose-700"
              }`}
            >
              {supported ? <CheckCircle2 className="h-3 w-3" /> : <XCircle className="h-3 w-3" />}
              {item.label}
            </span>
          );
        })}
      </div>
    </section>
  );
};

const SkillsSection: React.FC<{ skills?: any[] }> = ({ skills }) => {
  if (!skills?.length) return null;
  return (
    <section className="space-y-2">
      <h5 className="flex items-center gap-1.5 border-b border-neutral-100 pb-1 text-[11px] font-bold uppercase tracking-wide text-neutral-500">
        <Cpu className="h-3 w-3" />
        <span>Skills</span>
      </h5>
      <div className="divide-y divide-neutral-100 rounded-lg border border-neutral-100 bg-white">
        {skills.map((skill, index) => (
          <div key={index} className="p-3">
            <p className="text-[13px] font-semibold text-neutral-900">
              {skill.name || skill.skill_id || skill.id || "Skill"}
            </p>
            {skill.description && (
              <p className="mt-1 text-[12px] leading-relaxed text-neutral-600">{skill.description}</p>
            )}
            {skill.constraints?.spatial && (
              <p className="mt-1 text-[11px] text-neutral-600">
                <span className="font-semibold text-neutral-800">Spatial:</span> {skill.constraints.spatial}
              </p>
            )}
            {skill.constraints?.temporal && (
              <p className="mt-1 text-[11px] text-neutral-600">
                <span className="font-semibold text-neutral-800">Temporal:</span> {skill.constraints.temporal}
              </p>
            )}
            {Array.isArray(skill.constraints?.other) && skill.constraints.other.length > 0 && (
              <ul className="mt-1 list-disc pl-4 text-[11px] text-neutral-600">
                {skill.constraints.other.map((item: string) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
    </section>
  );
};

const ExecuteSection: React.FC<{ executeTask: any; legacyOps?: any[] }> = ({ executeTask, legacyOps }) => {
  const op = executeTask || (Array.isArray(legacyOps) ? legacyOps[0] : null);
  if (!op || typeof op !== "object") return null;

  const required: string[] = [];
  [op.task, op.inputs].forEach((block) => {
    if (!block || typeof block !== "object") return;
    Object.keys(block).forEach((key) => {
      if (block[key]?.required) required.push(key);
    });
  });

  const artifacts = Array.isArray(op.outputs?.primary_artifacts)
    ? op.outputs.primary_artifacts
        .map((artifact: any) => `${artifact.type || ""}${Array.isArray(artifact.formats) ? ` (${artifact.formats.join("/")})` : ""}`)
        .filter(Boolean)
    : [];

  return (
    <section className="space-y-2">
      <h5 className="flex items-center gap-1.5 border-b border-neutral-100 pb-1 text-[11px] font-bold uppercase tracking-wide text-neutral-500">
        <KeyRound className="h-3 w-3" />
        <span>How to invoke</span>
      </h5>
      <div className="space-y-1.5 text-[12px] leading-relaxed text-neutral-600">
        <p className="text-[13px] font-semibold text-neutral-900">{op.name || op.operation_id || "Operation"}</p>
        {op.description && <p>{op.description}</p>}
        {Array.isArray(op.modes) && op.modes.length > 0 && (
          <div className="space-y-1">
            <span className="font-semibold text-neutral-800">Modes:</span>
            <CodeList values={op.modes} />
          </div>
        )}
        {required.length > 0 && (
          <div className="space-y-1">
            <span className="font-semibold text-neutral-800">Required inputs:</span>
            <CodeList values={required} />
          </div>
        )}
        {artifacts.length > 0 && (
          <p><span className="font-semibold text-neutral-800">Artifacts:</span> {artifacts.join(", ")}</p>
        )}
        {Array.isArray(op.credentials?.one_of) && op.credentials.one_of.length > 0 && (
          <div className="space-y-1">
            <span className="font-semibold text-neutral-800">Credentials:</span>
            <CodeList values={op.credentials.one_of} />
          </div>
        )}
      </div>
    </section>
  );
};

export const AgentDescribePanel: React.FC<AgentDescribePanelProps> = ({ agentInfo, onClose, width }) => {
  if (!agentInfo || !agentInfo.profile) {
    return (
      <div style={{ width: `${width}px` }} className="flex h-full shrink-0 flex-col items-center justify-center border-l border-neutral-200 bg-white p-6 text-center">
        <Cpu className="mb-2 h-10 w-10 animate-pulse text-neutral-300" />
        <h4 className="text-sm font-semibold text-neutral-500">Loading Agent Profile...</h4>
      </div>
    );
  }

  const profile = agentInfo.profile || {};
  const server = agentInfo._server || {};
  const serverProvider = server.provider || {};
  const serverContact = serverProvider.contact || {};
  const agentProvider = profile.provider || {};
  const agentContact = agentProvider.contact || profile.contact || {};
  const pr = agentInfo.provenance_and_reproducibility || {};
  const title = profile.name || profile.agent_id || agentInfo.agent_id || "GAS Agent";
  const metaParts = [
    profile.version ? `Version ${profile.version}` : "",
    profile.status,
    profile.last_updated ? `Updated ${profile.last_updated}` : "",
  ].filter(Boolean);

  return (
    <div style={{ width: `${width}px` }} className="flex h-full shrink-0 flex-col overflow-hidden border-l border-neutral-200 bg-white shadow-xl">
      <div className="flex items-start justify-between border-b border-neutral-100 bg-white p-5">
        <div className="min-w-0 pr-3">
          <h4 className="truncate text-xl font-bold tracking-tight text-neutral-950" title={title}>
            {title}
          </h4>
          {metaParts.length > 0 && (
            <p className="mt-1 text-[12px] text-neutral-500">{metaParts.join(" · ")}</p>
          )}
        </div>
        <button
          onClick={onClose}
          className="shrink-0 rounded-full bg-neutral-100 p-2 text-neutral-500 transition-colors hover:bg-neutral-200 hover:text-neutral-800"
          aria-label="Close agent information"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="flex-1 space-y-5 overflow-y-auto p-5">
        {profile.description && (
          <p className="text-[14px] leading-relaxed text-neutral-700">{profile.description}</p>
        )}

        <FactsSection
          title="GAS Server Provider"
          icon={<Server className="h-3 w-3" />}
          compact
          rows={[
            ["Provider", <ProviderLink name={server.providerName || serverProvider.name} website={serverProvider.website} />],
            ["GetCapabilities URL", server.getCapabilitiesUrl ? (
              <a href={server.getCapabilitiesUrl} target="_blank" rel="noreferrer" className="text-sky-600 hover:underline">
                {server.getCapabilitiesUrl}
              </a>
            ) : ""],
            ["Contact", [serverContact.name, serverContact.email].filter(Boolean).join(", ")],
          ]}
        />

        <FactsSection
          title="Agent Provider"
          icon={<Box className="h-3 w-3" />}
          compact
          rows={[
            ["Provider", <ProviderLink name={agentProvider.name} website={agentProvider.website} />],
            ["Agent ID", profile.agent_id || agentInfo.agent_id],
            ["DescribeAgent URL", server.describeUrl ? (
              <a href={server.describeUrl} target="_blank" rel="noreferrer" className="text-sky-600 hover:underline">
                {server.describeUrl}
              </a>
            ) : ""],
            ["Default model", profile.default_model],
            ["Contact", [agentContact.name, agentContact.email].filter(Boolean).join(", ")],
          ]}
        />

        <TagsSection title="Keywords" values={agentInfo.keywords || []} />
        <CapabilityBadges pr={pr} />

        <FactsSection
          title="Geospatial data support"
          icon={<Database className="h-3 w-3" />}
          rows={[
            ["Data types", agentInfo.data_support?.supported_data_types],
            ["Formats", agentInfo.data_support?.supported_formats],
            ["CRS", agentInfo.data_support?.supported_crs || agentInfo.data_support?.coordinate_reference_systems],
            ["Geometry types", agentInfo.data_support?.supported_geometry_types || agentInfo.data_support?.geometry_types],
            ["Raster types", agentInfo.data_support?.supported_raster_types || agentInfo.data_support?.raster_types],
            ["Spatial extent", agentInfo.data_support?.spatial_extent || agentInfo.data_support?.extent],
            ["Scale / resolution", agentInfo.data_support?.scale_or_resolution || agentInfo.data_support?.resolution],
            ["Input datasets required", agentInfo.data_support?.requires_input_datasets],
          ]}
        />

        <SkillsSection skills={agentInfo.skills || []} />
        <ExecuteSection executeTask={agentInfo.execute_task} legacyOps={agentInfo.operations} />

        <FactsSection
          title="Governance and use constraints"
          icon={<ShieldCheck className="h-3 w-3" />}
          rows={[
            ["Access control", agentInfo.governance?.access || agentInfo.governance?.access_control],
            ["Operation policy", agentInfo.governance?.operation_policy],
            ["Review policy", agentInfo.governance?.review_policy],
            ["Policy notes", agentInfo.governance?.policy_notes],
            ["License", agentInfo.governance?.license || agentInfo.governance?.licensing],
            ["Privacy", agentInfo.governance?.privacy],
            ["Human review", agentInfo.governance?.human_review],
            ["Permitted use", agentInfo.governance?.permitted_use || agentInfo.governance?.permitted_uses],
            ["Data retention", agentInfo.governance?.data_retention || agentInfo.governance?.retention],
          ]}
        />

        <FactsSection
          title="Provenance and reproducibility details"
          icon={<Layers className="h-3 w-3" />}
          rows={[
            ["Provenance", pr.provenance],
            ["Reproducibility", pr.reproducibility],
            ["Validation", pr.validation],
          ]}
        />

        <FactsSection
          title="GAS conformance"
          icon={<FileJson className="h-3 w-3" />}
          rows={[
            ["GAS version", agentInfo.conformance?.gas_version],
            ["Interface profile", agentInfo.conformance?.interface_profile || agentInfo.conformance?.profile],
            ["Response schema", agentInfo.conformance?.response_schema],
            ["Request schema", agentInfo.conformance?.request_schema],
            ["Task modes", agentInfo.conformance?.task_modes || agentInfo.conformance?.modes],
          ]}
        />

        <FactsSection
          title="Agent-specific extensions"
          icon={<FileJson className="h-3 w-3" />}
          rows={Object.entries(agentInfo.extensions || {}).map(([key, value]) => [
            key.replace(/_/g, " "),
            Array.isArray(value)
              ? value.slice(0, 6).map((item: any) => typeof item === "string" ? item : item.name || item.id || item.title || JSON.stringify(item))
              : value as FactValue,
          ])}
        />
      </div>
    </div>
  );
};
