// ioredis singleton. We have two needs:
//   - normal command client (for LRANGE, GET, etc.)
//   - subscriber client (a connection in subscribe mode cannot issue
//     normal commands; ioredis enforces this)
//
// The WS server creates its own subscribers per connection. This module
// only exports the command-mode client.

import Redis from "ioredis";
import { env } from "./env";

let _client: Redis | null = null;

export function redis(): Redis {
  if (!_client) {
    _client = new Redis(env.REDIS_URL, {
      maxRetriesPerRequest: 3,
      enableReadyCheck: true,
      lazyConnect: false,
    });
  }
  return _client;
}
