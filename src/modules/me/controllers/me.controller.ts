import type {
  FastifyInstance,
  FastifyReply,
  FastifyRequest
} from "fastify";
import { AppRoute } from "../../../config/routes.js";
import type { AuthService } from "../../auth/auth.service.js";
import { createAuthGuard, type AuthGuard } from "../../../http/auth-guard.js";
import { docsRoute } from "../../../http/route.js";
import { handleCommonError } from "../../../http/errors.js";
import { meRouteSchema } from "../common/me.openapi.js";
import type { MeService } from "../common/me.types.js";

export class MeController {
  private readonly authGuard: AuthGuard;

  constructor(
    private readonly meService: MeService,
    authService: AuthService
  ) {
    this.authGuard = createAuthGuard(authService);
  }

  register(app: FastifyInstance) {
    app.get(AppRoute.Me, docsRoute(meRouteSchema), this.getMe.bind(this));
  }

  private async getMe(request: FastifyRequest, reply: FastifyReply) {
    const user = await this.authGuard.requireUser(request, reply);

    if (!user) {
      return reply;
    }

    try {
      return await this.meService(user);
    } catch (error) {
      return handleCommonError(request, reply, error);
    }
  }
}
