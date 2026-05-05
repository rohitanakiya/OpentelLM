"use client";
import { Copy, Plus, Trash2, KeyRound, Folder, User } from "lucide-react";
import { useEffect, useState } from "react";
import { CardListSkeleton } from "@/components/skeletons";
import { ErrorState, EmptyState } from "@/components/error-state";
import { fmtDate } from "@/lib/format";

type Project = { id: string; tenant_id: string; name: string; created_at: string };
type ApiKey  = {
  id: string; project_id: string; key_prefix: string;
  permissions: "read" | "write" | "admin";
  is_active: boolean; created_at: string; last_used_at: string | null;
};
type CreatedKey = ApiKey & { api_key: string };

export default function SettingsPage() {
  return (
    <div className="space-y-10">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="text-sm text-muted">Projects, API keys, and account preferences.</p>
      </header>

      <Section icon={Folder} title="Projects">
        <ProjectsPanel />
      </Section>

      <Section icon={KeyRound} title="API keys">
        <KeysPanel />
      </Section>

      <Section icon={User} title="Account">
        <AccountPanel />
      </Section>
    </div>
  );
}

function Section({
  icon: Icon, title, children,
}: {
  icon: React.ElementType;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-3">
      <div className="flex items-center gap-2">
        <Icon size={16} className="text-accent" />
        <h2 className="font-medium">{title}</h2>
      </div>
      {children}
    </section>
  );
}

// ---------- Projects ----------
function ProjectsPanel() {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError]       = useState<string | null>(null);
  const [name, setName]         = useState("");
  const [busy, setBusy]         = useState(false);

  async function load() {
    setError(null);
    try {
      const r = await fetch("/api/projects", { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setProjects(await r.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load projects");
    }
  }
  useEffect(() => { load(); }, []);

  async function create() {
    if (!name.trim()) return;
    setBusy(true);
    try {
      const r = await fetch("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim() }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setName("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "create failed");
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    if (!confirm("Delete this project? All its API keys will be revoked.")) return;
    await fetch(`/api/projects/${id}`, { method: "DELETE" });
    await load();
  }

  if (error)    return <ErrorState message={error} onRetry={load} />;
  if (!projects) return <CardListSkeleton count={2} />;

  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <input
          placeholder="new project name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="flex-1 bg-surface border border-border rounded-md px-3 py-2 text-sm focus:outline-none focus:border-accent"
        />
        <button
          onClick={create}
          disabled={busy || !name.trim()}
          className="flex items-center gap-1.5 px-3 py-2 rounded-md bg-accent hover:bg-accent/90 disabled:opacity-60 text-sm transition-colors"
        >
          <Plus size={14} /> Create
        </button>
      </div>
      {projects.length === 0 ? (
        <EmptyState
          icon={Folder}
          title="No projects yet"
          description="Create your first project to start ingesting events."
        />
      ) : (
        <div className="gradient-border divide-y divide-border">
          {projects.map((p) => (
            <div key={p.id} className="flex items-center gap-3 px-4 py-3">
              <div className="flex-1 min-w-0">
                <div className="font-medium">{p.name}</div>
                <div className="text-xs text-muted font-mono truncate">{p.id}</div>
              </div>
              <div className="text-xs text-muted">{fmtDate(p.created_at)}</div>
              <button
                onClick={() => remove(p.id)}
                className="text-muted hover:text-danger transition-colors"
                aria-label="Delete project"
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------- Keys ----------
function KeysPanel() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<CreatedKey | null>(null);

  async function loadProjects() {
    const r = await fetch("/api/projects", { cache: "no-store" });
    if (!r.ok) return;
    const list: Project[] = await r.json();
    setProjects(list);
    if (!selected && list.length) setSelected(list[0].id);
  }

  async function loadKeys() {
    if (!selected) { setKeys([]); return; }
    setError(null);
    try {
      const r = await fetch(`/api/projects/${selected}/keys`, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setKeys(await r.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load keys");
    }
  }

  useEffect(() => { loadProjects(); }, []);
  useEffect(() => { loadKeys(); }, [selected]);

  async function create(permissions: "read" | "write" | "admin") {
    if (!selected) return;
    const r = await fetch(`/api/projects/${selected}/keys`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ permissions }),
    });
    if (!r.ok) {
      setError(`create key failed (HTTP ${r.status})`);
      return;
    }
    const k: CreatedKey = await r.json();
    setCreated(k);
    await loadKeys();
  }

  async function revoke(keyId: string) {
    if (!selected) return;
    if (!confirm("Revoke this API key? The change is immediate.")) return;
    await fetch(`/api/projects/${selected}/keys/${keyId}`, { method: "DELETE" });
    await loadKeys();
  }

  if (projects.length === 0) {
    return (
      <EmptyState
        icon={KeyRound}
        title="Create a project first"
        description="API keys belong to projects."
      />
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex gap-2 flex-wrap">
        <select
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
          className="bg-surface border border-border rounded-md px-3 py-2 text-sm focus:outline-none focus:border-accent"
        >
          {projects.map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>
        <button
          onClick={() => create("write")}
          className="flex items-center gap-1.5 px-3 py-2 rounded-md bg-accent hover:bg-accent/90 text-sm transition-colors"
        >
          <Plus size={14} /> New write key
        </button>
        <button
          onClick={() => create("read")}
          className="flex items-center gap-1.5 px-3 py-2 rounded-md border border-border hover:bg-surface2 text-sm transition-colors"
        >
          <Plus size={14} /> Read-only key
        </button>
      </div>

      {created ? (
        <div className="gradient-border p-4 border-accent/40">
          <div className="text-sm font-medium text-accent mb-1">Save this key now</div>
          <div className="text-xs text-muted mb-3">
            This is the only time we&apos;ll show the full key. Treat it like a password.
          </div>
          <div className="flex items-center gap-2">
            <code className="flex-1 font-mono text-xs bg-bg border border-border rounded px-3 py-2 break-all">
              {created.api_key}
            </code>
            <button
              onClick={() => navigator.clipboard.writeText(created.api_key)}
              className="px-2 py-2 border border-border rounded-md hover:bg-surface2 transition-colors"
              aria-label="Copy"
            >
              <Copy size={14} />
            </button>
            <button
              onClick={() => setCreated(null)}
              className="px-3 py-2 text-sm border border-border rounded-md hover:bg-surface2 transition-colors"
            >
              Done
            </button>
          </div>
        </div>
      ) : null}

      {error ? <ErrorState message={error} onRetry={loadKeys} /> : null}

      {!keys ? (
        <CardListSkeleton count={2} />
      ) : keys.length === 0 ? (
        <EmptyState
          icon={KeyRound}
          title="No keys for this project"
          description="Create one above to start ingesting."
        />
      ) : (
        <div className="gradient-border overflow-hidden">
          <table className="w-full text-sm">
            <thead className="text-xs uppercase text-muted bg-surface2/40">
              <tr>
                <th className="text-left px-4 py-2.5">Prefix</th>
                <th className="text-left px-4 py-2.5">Permissions</th>
                <th className="text-left px-4 py-2.5">Created</th>
                <th className="text-left px-4 py-2.5">Last used</th>
                <th className="text-left px-4 py-2.5">Status</th>
                <th className="w-8" />
              </tr>
            </thead>
            <tbody>
              {keys.map((k) => (
                <tr key={k.id} className="border-t border-border">
                  <td className="px-4 py-2.5 font-mono text-xs">{k.key_prefix}…</td>
                  <td className="px-4 py-2.5">{k.permissions}</td>
                  <td className="px-4 py-2.5 text-muted">{fmtDate(k.created_at)}</td>
                  <td className="px-4 py-2.5 text-muted">{k.last_used_at ? fmtDate(k.last_used_at) : "—"}</td>
                  <td className="px-4 py-2.5">
                    <span className={`text-xs px-2 py-0.5 rounded border ${
                      k.is_active
                        ? "bg-success/15 text-success border-success/30"
                        : "bg-muted/10 text-muted border-border"
                    }`}>
                      {k.is_active ? "active" : "revoked"}
                    </span>
                  </td>
                  <td className="px-2 text-right">
                    {k.is_active ? (
                      <button
                        onClick={() => revoke(k.id)}
                        className="text-muted hover:text-danger transition-colors"
                        aria-label="Revoke"
                      >
                        <Trash2 size={14} />
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---------- Account ----------
function AccountPanel() {
  const [current, setCurrent] = useState("");
  const [next, setNext]       = useState("");
  const [msg, setMsg]         = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [busy, setBusy]       = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      const r = await fetch("/api/password", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_password: current, new_password: next }),
      });
      if (r.status === 204) {
        setCurrent(""); setNext("");
        setMsg({ kind: "ok", text: "Password updated. Other sessions have been signed out." });
      } else {
        const data = await r.json().catch(() => ({}));
        const err  = data?.detail?.error ?? data?.error ?? "update_failed";
        setMsg({ kind: "err", text: `Failed: ${err}` });
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="gradient-border p-5 max-w-md space-y-4">
      <div>
        <label className="block text-xs uppercase tracking-wider text-muted mb-1">Current password</label>
        <input
          type="password"
          required
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          className="w-full bg-bg border border-border rounded-md px-3 py-2 text-sm focus:outline-none focus:border-accent"
        />
      </div>
      <div>
        <label className="block text-xs uppercase tracking-wider text-muted mb-1">New password</label>
        <input
          type="password"
          minLength={8}
          required
          value={next}
          onChange={(e) => setNext(e.target.value)}
          className="w-full bg-bg border border-border rounded-md px-3 py-2 text-sm focus:outline-none focus:border-accent"
        />
      </div>
      {msg ? (
        <div className={`text-sm rounded-md border px-3 py-2 ${
          msg.kind === "ok"
            ? "bg-success/10 text-success border-success/30"
            : "bg-danger/10 text-danger border-danger/30"
        }`}>{msg.text}</div>
      ) : null}
      <button
        type="submit"
        disabled={busy || !current || !next}
        className="px-4 py-2 rounded-md bg-accent hover:bg-accent/90 disabled:opacity-60 text-sm transition-colors"
      >
        {busy ? "Updating…" : "Update password"}
      </button>
    </form>
  );
}


