import { fileURLToPath } from "node:url";

import { Pool } from "../frontend/node_modules/pg";
import dotenv from "../frontend/node_modules/dotenv/lib/main";

const envPath = fileURLToPath(new URL("../frontend/.env.local", import.meta.url));
dotenv.config({ path: envPath });

const email = process.env.SEED_USER_EMAIL ?? "admin@alchemy.dev";
const password = process.env.SEED_USER_PASSWORD ?? "ChangeMe12345!";
const name = process.env.SEED_USER_NAME ?? "Alchemy Admin";
const role = process.env.SEED_USER_ROLE ?? "admin";

async function run() {
  const { auth } = await import("../frontend/lib/auth");
  const pool = new Pool({
    connectionString: process.env.DATABASE_URL
  });

  try {
    await auth.api.signUpEmail({
      body: {
        email,
        password,
        name
      }
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (!message.toLowerCase().includes("already")) {
      throw error;
    }
  }

  await pool.query('UPDATE "user" SET role = $1 WHERE email = $2', [role, email]);
  console.log(`Seeded BetterAuth user ${email} with role ${role}.`);
  await pool.end();
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
