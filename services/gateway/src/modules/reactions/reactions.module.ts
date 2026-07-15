import type { FastifyInstance } from "fastify";
import {
  supabaseAuthService,
  type AuthService
} from "../auth/auth.service.js";
import type { ReactionsServiceContract } from "./common/reactions.types.js";
import { ReactionsController } from "./controllers/reactions.controller.js";
import { reactionsService } from "./services/reactions.service.js";

export type ReactionsModuleOptions = {
  authService?: AuthService;
  reactionsService?: ReactionsServiceContract;
};

export async function registerReactionsModule(
  app: FastifyInstance,
  options: ReactionsModuleOptions = {}
) {
  const controller = new ReactionsController(
    options.reactionsService ?? reactionsService,
    options.authService ?? supabaseAuthService
  );

  controller.register(app);
}
