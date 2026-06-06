import type { FastifyInstance } from "fastify";
import { supabaseAuthService, type AuthService } from "../auth/auth.service.js";
import { MeController } from "./controllers/me.controller.js";
import { getMe } from "./services/me.service.js";
import type { MeService } from "./common/me.types.js";

export type MeModuleOptions = {
  authService?: AuthService;
  meService?: MeService;
};

export async function registerMeModule(
  app: FastifyInstance,
  options: MeModuleOptions = {}
) {
  const controller = new MeController(
    options.meService ?? getMe,
    options.authService ?? supabaseAuthService
  );

  controller.register(app);
}
