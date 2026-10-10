// Initialises the SDK and then idles, to check signals still stop the process.
import opentelm from "opentelm-node";
await opentelm.init({ apiKey: "k", endpoint: "http://127.0.0.1:9" });
setInterval(() => {}, 1000);
console.log("ready");
