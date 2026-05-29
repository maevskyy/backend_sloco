import cors from "@fastify/cors";
import Fastify from "fastify";
import { registerHealthRoutes } from "./modules/health/health.routes.js";

export async function buildApp() {
  const app = Fastify({
    logger: true
  });

  await app.register(cors, {
    origin: true
  });

  await app.register(registerHealthRoutes);

  return app;
}
