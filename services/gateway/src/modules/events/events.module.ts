import type { FastifyInstance } from "fastify";
import type { Db } from "../../lib/db.js";
import {
  supabaseAuthService,
  type AuthService
} from "../auth/auth.service.js";
import { EventsController } from "./controllers/events.controller.js";
import { createEventsService } from "./services/events.service.js";
import { EventsStore } from "./stores/events.store.js";
import type { EventsServiceContract } from "./common/events.types.js";

export type EventsModuleOptions = {
  authService?: AuthService;
  eventsService?: EventsServiceContract;
  // Shared direct-Postgres pool; stores fall back to getDb() without it.
  db?: Db;
};

export async function registerEventsModule(
  app: FastifyInstance,
  options: EventsModuleOptions = {}
) {
  const controller = new EventsController(
    options.eventsService ?? createEventsService(new EventsStore(options.db)),
    options.authService ?? supabaseAuthService
  );

  controller.register(app);
}
