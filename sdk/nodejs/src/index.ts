// Public API surface. Anything not exported here is private.

export { init, ready, setVersion, getVersion, flush, shutdown, track } from "./client";
export type { InitConfig, ManualEvent } from "./client";
export type { LLMEvent } from "./flush";
export { scrubPii } from "./pii";
export { SDK_VERSION } from "./version";

import * as client from "./client";

// Default export so users can write either:
//   import opentelm from "opentelm-node";      opentelm.init({ ... })
//   import { init } from "opentelm-node";      init({ ... })
const opentelm = {
  init: client.init,
  ready: client.ready,
  setVersion: client.setVersion,
  getVersion: client.getVersion,
  flush: client.flush,
  shutdown: client.shutdown,
  track: client.track,
};

export default opentelm;
