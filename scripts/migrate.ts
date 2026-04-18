/**
 * Apply the Postgres schema. Idempotent - uses CREATE ... IF NOT EXISTS.
 *
 * Usage:
 *   DATABASE_URL=postgres://... npm run db:migrate
 */
import "dotenv/config";
import pg from "pg";
import { migrate } from "../packages/state/src/postgres.js";

async function main(): Promise<void> {
  const url = process.env.DATABASE_URL;
  if (!url) {
    console.error("DATABASE_URL is required");
    process.exit(2);
  }
  const pool = new pg.Pool({ connectionString: url });
  try {
    await migrate(pool);
    console.log("migration ok");
  } finally {
    await pool.end();
  }
}

main().catch((e) => {
  console.error("migration failed", e);
  process.exit(1);
});
