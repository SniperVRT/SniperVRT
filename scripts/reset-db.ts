/**
 * Drop every orchestrator table and re-apply the schema. Destructive. Intended
 * for local dev and CI fixtures only.
 *
 * Usage:
 *   DATABASE_URL=postgres://... npm run db:reset
 */
import "dotenv/config";
import pg from "pg";
import { migrate } from "../packages/state/src/postgres.js";

const DROP_SQL = `
DROP TABLE IF EXISTS traces CASCADE;
DROP TABLE IF EXISTS approvals CASCADE;
DROP TABLE IF EXISTS steps CASCADE;
DROP TABLE IF EXISTS tasks CASCADE;
DROP TABLE IF EXISTS memory_entries CASCADE;
DROP TABLE IF EXISTS connectors CASCADE;
`;

async function main(): Promise<void> {
  const url = process.env.DATABASE_URL;
  if (!url) {
    console.error("DATABASE_URL is required");
    process.exit(2);
  }
  if (!process.env.ALLOW_DB_RESET) {
    console.error("refusing to reset without ALLOW_DB_RESET=1");
    process.exit(2);
  }
  const pool = new pg.Pool({ connectionString: url });
  try {
    await pool.query(DROP_SQL);
    await migrate(pool);
    console.log("reset ok");
  } finally {
    await pool.end();
  }
}

main().catch((e) => {
  console.error("reset failed", e);
  process.exit(1);
});
