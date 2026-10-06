import type { FastifyInstance } from "fastify";
import type { Db } from "../../lib/db.js";
import {
  supabaseAuthService,
  type AuthService
} from "../auth/auth.service.js";
import type { ReviewsServiceContract } from "./common/reviews.types.js";
import { ReviewsController } from "./controllers/reviews.controller.js";
import { createReviewsService } from "./services/reviews.service.js";
import { ReviewsStore } from "./stores/reviews.store.js";

export type ReviewsModuleOptions = {
  authService?: AuthService;
  reviewsService?: ReviewsServiceContract;
  // Shared direct-Postgres pool; stores fall back to getDb() without it.
  db?: Db;
};

export async function registerReviewsModule(
  app: FastifyInstance,
  options: ReviewsModuleOptions = {}
) {
  const controller = new ReviewsController(
    options.reviewsService ??
      createReviewsService(new ReviewsStore(options.db)),
    options.authService ?? supabaseAuthService
  );

  controller.register(app);
}
