import type {
  FastifyInstance,
  FastifyReply,
  FastifyRequest
} from "fastify";
import { AppRoute, VersionedAppRoute } from "../../../config/routes.js";
import type { AuthService } from "../../auth/auth.service.js";
import { createAuthGuard, type AuthGuard } from "../../../http/auth-guard.js";
import { docsRoute } from "../../../http/route.js";
import { handleCommonError } from "../../../http/errors.js";
import {
  LogMessagePrefix,
  logResponseSummary
} from "../../../http/response-log.js";
import { onboardingCompleteRouteSchema } from "../common/onboarding.openapi.js";
import { onboardingCompleteBodySchema } from "../common/onboarding.schemas.js";
import type { OnboardingServiceContract } from "../common/onboarding.types.js";

export class OnboardingController {
  private readonly authGuard: AuthGuard;

  constructor(
    private readonly service: OnboardingServiceContract,
    authService: AuthService
  ) {
    this.authGuard = createAuthGuard(authService);
  }

  register(app: FastifyInstance) {
    app.post(
      AppRoute.OnboardingComplete,
      docsRoute(onboardingCompleteRouteSchema),
      this.completeOnboarding.bind(this)
    );
  }

  private async completeOnboarding(
    request: FastifyRequest,
    reply: FastifyReply
  ) {
    const user = await this.authGuard.requireUser(request, reply);

    if (!user) {
      return reply;
    }

    try {
      const body = onboardingCompleteBodySchema.parse(request.body);
      const result = await this.service.completeOnboarding(user.id, body);

      logResponseSummary(
        request,
        VersionedAppRoute.onboardingComplete,
        {
          onboardingStatus: result.onboardingStatus,
          pickedCount: body.pickedPlaceIds.length,
          savedCount: result.savedCount
        },
        `${LogMessagePrefix.Response} ${VersionedAppRoute.onboardingComplete} ${result.onboardingStatus} saved=${result.savedCount}`
      );

      return result;
    } catch (error) {
      return handleCommonError(
        request,
        reply,
        error,
        "Invalid onboarding request"
      );
    }
  }
}
