// Authenticated layout — wraps every protected page with the sidebar and
// resolves the current tenant server-side. Middleware has already
// guaranteed there's a valid access cookie by the time we render here.

import { redirect } from "next/navigation";
import { getCurrentTenant } from "@/lib/auth";
import { Sidebar } from "@/components/sidebar";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const claims = await getCurrentTenant();
  if (!claims) redirect("/login");

  return (
    <div className="flex min-h-screen">
      <Sidebar email={claims.email} />
      <main className="flex-1 px-4 md:px-8 py-6 md:py-10 max-w-[1400px] mx-auto w-full">
        {children}
      </main>
    </div>
  );
}
