import type { FastifyInstance } from "fastify";
import { supabaseAuthService, type AuthService } from "../auth/auth.service.js";
import { FeedController } from "./controllers/feed.controller.js";
import { getFeedPlaces } from "./services/feed.service.js";
import type { FeedPlacesService } from "./common/feed.types.js";

export type FeedModuleOptions = {
  feedPlacesService?: FeedPlacesService;
  authService?: AuthService;
};

export async function registerFeedModule(
  app: FastifyInstance,
  options: FeedModuleOptions = {}
) {
  const controller = new FeedController(
    options.feedPlacesService ?? getFeedPlaces,
    options.authService ?? supabaseAuthService
  );

  controller.register(app);
}
