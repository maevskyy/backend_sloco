import type { FastifyInstance } from "fastify";
import {
  supabaseAuthService,
  type AuthService
} from "../auth/auth.service.js";
import { EventsController } from "./controllers/events.controller.js";
import { eventsService } from "./services/events.service.js";
import type { EventsServiceContract } from "./common/events.types.js";

export type EventsModuleOptions = {
  authService?: AuthService;
  eventsService?: EventsServiceContract;
};

export async function registerEventsModule(
  app: FastifyInstance,
  options: EventsModuleOptions = {}
) {
  const controller = new EventsController(
    options.eventsService ?? eventsService,
    options.authService ?? supabaseAuthService
  );

  controller.register(app);
}
