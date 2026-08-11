import type { FastifyInstance } from "fastify";
import {
  supabaseAuthService,
  type AuthService
} from "../auth/auth.service.js";
import { OnboardingController } from "./controllers/onboarding.controller.js";
import { onboardingService } from "./services/onboarding.service.js";
import type { OnboardingServiceContract } from "./common/onboarding.types.js";

export type OnboardingModuleOptions = {
  authService?: AuthService;
  onboardingService?: OnboardingServiceContract;
};

export async function registerOnboardingModule(
  app: FastifyInstance,
  options: OnboardingModuleOptions = {}
) {
  const controller = new OnboardingController(
    options.onboardingService ?? onboardingService,
    options.authService ?? supabaseAuthService
  );

  controller.register(app);
}
