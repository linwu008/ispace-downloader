// Isolated local release environment. Never connects to production D1/R2/mail/AI.
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
process.env.PORT = process.env.PORT || "18810";
process.env.COURSENEST_DB = resolve(
  fileURLToPath(new URL("../.runtime/v1-staging/", import.meta.url)),
  "staging.sqlite3",
);
process.env.COURSENEST_PRODUCTION_LIKE = "1";
await import("./dev.mjs");
