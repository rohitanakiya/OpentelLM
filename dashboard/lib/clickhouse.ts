// ClickHouse client singleton.
//
// IMPORTANT: the Node.js @clickhouse/client API is NOT the same as the
// Python clickhouse-driver. Always use:
//
//   const rs = await ch.query({ query: "SELECT ...", format: "JSONEachRow" });
//   const rows = await rs.json<Row>();
//
// Pass parameters via `query_params`, never string interpolation:
//
//   await ch.query({
//     query: "SELECT * FROM foo WHERE tenant_id = {tenant:String}",
//     query_params: { tenant: tenantId },
//     format: "JSONEachRow",
//   });

import { createClient, type ClickHouseClient } from "@clickhouse/client";
import { env } from "./env";

let _client: ClickHouseClient | null = null;

export function ch(): ClickHouseClient {
  if (!_client) {
    _client = createClient({
      url: env.CLICKHOUSE_URL,
      database: env.CLICKHOUSE_DB,
      username: env.CLICKHOUSE_USER,
      password: env.CLICKHOUSE_PASSWORD,
      // Bound the request to keep dashboards snappy
      request_timeout: 10_000,
    });
  }
  return _client;
}

/** Convenience wrapper that returns parsed JSONEachRow result. */
export async function chQuery<T>(
  query: string,
  params: Record<string, unknown> = {},
): Promise<T[]> {
  const rs = await ch().query({
    query,
    query_params: params,
    format: "JSONEachRow",
  });
  return rs.json<T>();
}
