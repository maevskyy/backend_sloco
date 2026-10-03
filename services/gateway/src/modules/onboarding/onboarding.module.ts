import type { FastifyInstance } from "fastify";
import type { Db } from "../../lib/db.js";
import {
  supabaseAuthService,
  type AuthService
} from "../auth/auth.service.js";
import { OnboardingController } from "./controllers/onboarding.controller.js";
import { createOnboardingService } from "./services/onboarding.service.js";
import { OnboardingStore } from "./stores/onboarding.store.js";
import type { OnboardingServiceContract } from "./common/onboarding.types.js";

export type OnboardingModuleOptions = {
  authService?: AuthService;
  onboardingService?: OnboardingServiceContract;
  // Shared direct-Postgres pool; stores fall back to getDb() without it.
  db?: Db;
};

export async function registerOnboardingModule(
  app: FastifyInstance,
  options: OnboardingModuleOptions = {}
) {
  const controller = new OnboardingController(
    options.onboardingService ??
      createOnboardingService(new OnboardingStore(options.db)),
    options.authService ?? supabaseAuthService
  );

  controller.register(app);
}
