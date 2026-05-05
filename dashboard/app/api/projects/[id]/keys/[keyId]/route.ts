import { NextRequest } from "next/server";
import { proxyToAuth } from "@/lib/upstream";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function DELETE(
  req: NextRequest,
  ctx: { params: { id: string; keyId: string } },
) {
  return proxyToAuth(
    req,
    `/projects/${ctx.params.id}/keys/${ctx.params.keyId}`,
    { method: "DELETE" },
  );
}
