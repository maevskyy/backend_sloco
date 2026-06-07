import type {
  FastifyInstance,
  FastifyReply,
  FastifyRequest
} from "fastify";
import { AppRoute, VersionedAppRoute } from "../../../config/routes.js";
import type { AuthService } from "../../auth/auth.service.js";
import { createAuthGuard, type AuthGuard } from "../../../http/auth-guard.js";
import { docsRoute } from "../../../http/route.js";
import { handleCommonError, unauthorizedResponse } from "../../../http/errors.js";
import {
  logResponseSummary,
  LogMessagePrefix
} from "../../../http/response-log.js";
import {
  mapConfigRouteSchema,
  mapPlacesRouteSchema,
  mapTileRouteSchema
} from "../common/map.openapi.js";
import { mapPlacesQuerySchema } from "../common/map.schemas.js";
import {
  MAP_TILE_CONTENT_TYPE,
  getMapTileUrlTemplate,
  mapTileParamsSchema,
  type MapTileService
} from "../common/map.tiles.js";
import { env } from "../../../config/env.js";
import type { MapPlacesService } from "../common/map.types.js";
import { enrichSavedState } from "../services/map.service.js";
import type { SavedPlacesService } from "../../saved-places/index.js";

export class MapController {
  private readonly authGuard: AuthGuard;

  constructor(
    private readonly mapPlacesService: MapPlacesService,
    private readonly mapTileService: MapTileService,
    private readonly savedPlacesService: SavedPlacesService,
    authService: AuthService
  ) {
    this.authGuard = createAuthGuard(authService);
  }

  register(app: FastifyInstance) {
    app.get(
      AppRoute.MapPlaces,
      docsRoute(mapPlacesRouteSchema),
      this.getMapPlaces.bind(this)
    );
    app.get(
      AppRoute.MapConfig,
      docsRoute(mapConfigRouteSchema),
      this.getMapConfig.bind(this)
    );
    app.get(
      AppRoute.MapTile,
      docsRoute(mapTileRouteSchema),
      this.getMapTile.bind(this)
    );
  }

  private async getMapPlaces(request: FastifyRequest, reply: FastifyReply) {
    try {
      const user = await this.authGuard.optionalUser(request);

      if (user === "invalid") {
        return reply.code(401).send(unauthorizedResponse);
      }

      const query = mapPlacesQuerySchema.parse(request.query);
      const result = await enrichSavedState(
        await this.mapPlacesService(query),
        user?.id,
        this.savedPlacesService
      );

      logResponseSummary(
        request,
        VersionedAppRoute.mapPlaces,
        {
          zoom: query.zoom,
          limit: query.limit,
          placesCount: result.places.length,
          capped: result.meta.capped,
          capHit: result.meta.capHit,
          candidateLimit: result.meta.candidateLimit,
          minScore: result.meta.minScore,
          bbox: {
            swLat: query.swLat,
            swLng: query.swLng,
            neLat: query.neLat,
            neLng: query.neLng
          }
        },
        `${LogMessagePrefix.Response} ${VersionedAppRoute.mapPlaces} ${result.places.length} places`
      );

      return result;
    } catch (error) {
      return handleCommonError(
        request,
        reply,
        error,
        "Invalid map places query"
      );
    }
  }

  private async getMapConfig() {
    return {
      tileVersion: env.MAP_TILE_VERSION,
      tileUrlTemplate: getMapTileUrlTemplate(env.MAP_TILE_VERSION),
      sourceLayer: "places" as const
    };
  }

  private async getMapTile(request: FastifyRequest, reply: FastifyReply) {
    try {
      const params = mapTileParamsSchema.parse(request.params);
      const result = await this.mapTileService(params);

      reply
        .header("Cache-Control", result.cacheControl)
        .header("ETag", result.etag);

      if (request.headers["if-none-match"] === result.etag) {
        return reply.code(304).send();
      }

      if (result.statusCode === 204) {
        return reply.code(204).send();
      }

      logResponseSummary(
        request,
        VersionedAppRoute.mapTile,
        {
          z: params.z,
          x: params.x,
          y: params.y,
          bytes: result.body.length
        },
        `${LogMessagePrefix.Response} ${VersionedAppRoute.mapTile} ${params.z}/${params.x}/${params.y}`
      );

      return reply
        .code(200)
        .type(MAP_TILE_CONTENT_TYPE)
        .send(result.body);
    } catch (error) {
      return handleCommonError(request, reply, error, "Invalid map tile request");
    }
  }
}
