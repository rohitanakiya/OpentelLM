"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity, AlertTriangle, BarChart3, FileText, Layers, LogOut, Menu, Settings, X,
} from "lucide-react";
import { useState } from "react";

const NAV = [
  { href: "/overview", label: "Overview", icon: Activity },
  { href: "/traces",   label: "Traces",   icon: FileText },
  { href: "/alerts",   label: "Alerts",   icon: AlertTriangle },
  { href: "/models",   label: "Models",   icon: Layers },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function Sidebar({ email }: { email: string }) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    window.location.href = "/login";
  }

  const linkCls = (href: string) => {
    const active = pathname === href || pathname?.startsWith(`${href}/`);
    return [
      "flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors",
      active
        ? "bg-accent/15 text-white border border-accent/40"
        : "text-muted hover:text-white hover:bg-surface2",
    ].join(" ");
  };

  return (
    <>
      {/* Mobile: hamburger */}
      <button
        className="md:hidden fixed top-3 left-3 z-50 rounded-md bg-surface p-2 border border-border"
        onClick={() => setOpen(!open)}
        aria-label="Toggle navigation"
      >
        {open ? <X size={18} /> : <Menu size={18} />}
      </button>

      <aside
        className={[
          "fixed md:static inset-y-0 left-0 z-40 w-60 bg-surface border-r border-border",
          "flex flex-col transition-transform duration-200",
          open ? "translate-x-0" : "-translate-x-full md:translate-x-0",
        ].join(" ")}
      >
        <div className="px-5 py-5 border-b border-border">
          <Link href="/overview" className="flex items-center gap-2">
            <BarChart3 size={20} className="text-accent" />
            <span className="font-semibold tracking-tight">OpenTelLM</span>
          </Link>
        </div>

        <nav className="flex-1 p-3 space-y-1">
          {NAV.map(({ href, label, icon: Icon }) => (
            <Link key={href} href={href} className={linkCls(href)} onClick={() => setOpen(false)}>
              <Icon size={16} />
              {label}
            </Link>
          ))}
        </nav>

        <div className="p-3 border-t border-border">
          <div className="px-3 py-2 text-xs text-muted truncate" title={email}>
            {email}
          </div>
          <button
            onClick={logout}
            className="flex items-center gap-2 w-full px-3 py-2 rounded-md text-sm text-muted hover:text-white hover:bg-surface2 transition-colors"
          >
            <LogOut size={14} />
            Sign out
          </button>
        </div>
      </aside>
    </>
  );
}
