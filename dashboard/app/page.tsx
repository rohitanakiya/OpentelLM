import { redirect } from "next/navigation";
import { getCurrentTenant } from "@/lib/auth";

// Root → /overview if logged in, /login otherwise
export default async function Home() {
  const claims = await getCurrentTenant();
  redirect(claims ? "/overview" : "/login");
}
