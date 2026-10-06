import type {
  FastifyInstance,
  FastifyReply,
  FastifyRequest
} from "fastify";
import { ZodError } from "zod";
import { AppRoute, VersionedAppRoute } from "../../../config/routes.js";
import { createAuthGuard, type AuthGuard } from "../../../http/auth-guard.js";
import { handleCommonError } from "../../../http/errors.js";
import { docsRoute } from "../../../http/route.js";
import {
  LogMessagePrefix,
  logResponseSummary
} from "../../../http/response-log.js";
import type { AuthService, AuthenticatedUser } from "../../auth/auth.service.js";
import {
  PlaceNotFoundError,
  ReviewPhotoNotFoundError
} from "../common/reviews.errors.js";
import * as openApi from "../common/reviews.openapi.js";
import * as schemas from "../common/reviews.schemas.js";
import type { ReviewsServiceContract } from "../common/reviews.types.js";

const placeNotFoundResponse = {
  status: "error",
  message: "Place not found"
} as const;

const validationMessage = "Invalid review request";

export class ReviewsController {
  private readonly authGuard: AuthGuard;

  constructor(
    private readonly service: ReviewsServiceContract,
    authService: AuthService
  ) {
    this.authGuard = createAuthGuard(authService);
  }

  register(app: FastifyInstance) {
    app.get(
      AppRoute.MeReviews,
      docsRoute(openApi.listReviewsRouteSchema),
      this.listReviews.bind(this)
    );
    app.put(
      AppRoute.MePlaceReview,
      docsRoute(openApi.upsertReviewRouteSchema),
      this.upsertReview.bind(this)
    );
    app.delete(
      AppRoute.MePlaceReview,
      docsRoute(openApi.deleteReviewRouteSchema),
      this.deleteReview.bind(this)
    );
  }

  private async listReviews(request: FastifyRequest, reply: FastifyReply) {
    return this.withUser(request, reply, async (user) => {
      const page = schemas.listReviewsQuerySchema.parse(request.query);
      const result = await this.service.listReviews(user.id, page);

      logResponseSummary(
        request,
        VersionedAppRoute.meReviews,
        {
          reviewsCount: result.reviews.length,
          total: result.total,
          limit: page.limit,
          offset: page.offset
        },
        `${LogMessagePrefix.Response} ${VersionedAppRoute.meReviews} ${result.reviews.length} of ${result.total} reviews`
      );

      return result;
    });
  }

  private async upsertReview(request: FastifyRequest, reply: FastifyReply) {
    return this.withUser(request, reply, async (user) => {
      const { placeId } = schemas.reviewParamsSchema.parse(request.params);
      const body = schemas.upsertReviewBodySchema.parse(request.body);
      const result = await this.service.upsertReview(user.id, placeId, body);

      logResponseSummary(
        request,
        VersionedAppRoute.mePlaceReview,
        {
          placeId,
          rating: result.review.rating,
          tagsCount: result.review.tags.length,
          textLength: result.review.text.length
        },
        `${LogMessagePrefix.Response} ${VersionedAppRoute.mePlaceReview} ${result.review.rating} stars for place ${placeId}`
      );

      return result;
    });
  }

  private async deleteReview(request: FastifyRequest, reply: FastifyReply) {
    return this.withUser(request, reply, async (user) => {
      const { placeId } = schemas.reviewParamsSchema.parse(request.params);
      await this.service.deleteReview(user.id, placeId);

      logResponseSummary(
        request,
        VersionedAppRoute.mePlaceReview,
        {
          placeId,
          deleted: true
        },
        `${LogMessagePrefix.Response} ${VersionedAppRoute.mePlaceReview} deleted for place ${placeId}`
      );

      return reply.code(204).send();
    });
  }

  private async withUser<T>(
    request: FastifyRequest,
    reply: FastifyReply,
    handler: (user: AuthenticatedUser) => Promise<T>
  ) {
    const user = await this.authGuard.requireUser(request, reply);

    if (!user) {
      return reply;
    }

    try {
      return await handler(user);
    } catch (error) {
      return this.handleError(request, reply, error);
    }
  }

  private handleError(
    request: FastifyRequest,
    reply: FastifyReply,
    error: unknown
  ) {
    if (error instanceof PlaceNotFoundError) {
      return reply.code(404).send(placeNotFoundResponse);
    }

    // The iOS spec asks for 422 on invalid input (other modules answer 400).
    if (error instanceof ZodError) {
      return reply.code(422).send({
        status: "error",
        message: validationMessage,
        issues: error.issues
      });
    }

    if (error instanceof ReviewPhotoNotFoundError) {
      return reply.code(422).send({
        status: "error",
        message: validationMessage,
        issues: error.photoIds.map((photoId) => ({
          code: "unknown_photo",
          path: ["photoIds"],
          message: `Photo ${photoId} was not uploaded by this user`
        }))
      });
    }

    return handleCommonError(request, reply, error, validationMessage);
  }
}
