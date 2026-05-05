// Public API surface. Anything not exported here is private.

export { init, setVersion, getVersion, flush, shutdown, track } from "./client";
export type { InitConfig, ManualEvent } from "./client";
export type { LLMEvent } from "./flush";

import * as client from "./client";

// Default export so users can write either:
//   import opentelm from "opentelm-node";
//   opentelm.init({ apiKey: "..." });
// or
//   import { init } from "opentelm-node";
const opentelm = {
  init:        client.init,
  setVersion:  client.setVersion,
  getVersion:  client.getVersion,
  flush:       client.flush,
  shutdown:    client.shutdown,
  track:       client.track,
};

export default opentelm;
