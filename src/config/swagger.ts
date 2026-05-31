import swagger from "@fastify/swagger";
import swaggerUi from "@fastify/swagger-ui";
import type { FastifyInstance } from "fastify";
import {
  errorResponseSchema,
  healthStatusResponseSchema
} from "../modules/health/health.openapi.js";
import {
  mapPlaceSchema,
  mapPlacesQuerySchemaOpenApi,
  mapPlacesResponseSchema,
  validationErrorResponseSchema
} from "../modules/map/map.openapi.js";
import {
  authErrorResponseSchema,
  meProfileSchema,
  meResponseSchema,
  meUserSchema
} from "../modules/me/me.openapi.js";
import {
  addPlaceToCollectionBodySchemaOpenApi,
  deleteCollectionResponseSchema,
  removePlaceFromCollectionResponseSchema,
  reorderCollectionPlacesBodySchemaOpenApi,
  reorderCollectionPlacesResponseSchema,
  savePlaceBodySchemaOpenApi,
  notFoundResponseSchema,
  savePlaceResponseSchema,
  savedCollectionBodySchemaOpenApi,
  savedCollectionCompactSchema,
  savedCollectionDetailResponseSchema,
  savedCollectionDetailSchema,
  savedCollectionParamsSchemaOpenApi,
  savedCollectionPlaceParamsSchemaOpenApi,
  savedCollectionResponseSchema,
  savedCollectionSchema,
  savedDashboardResponseSchema,
  savedPlaceParamsSchemaOpenApi,
  savedPlaceSummarySchema,
  unsavePlaceResponseSchema,
  updateSavedCollectionBodySchemaOpenApi
} from "../modules/saved-places/saved-places.openapi.js";
import { VersionedAppRoute } from "./routes.js";

type AppWithSwagger = FastifyInstance & {
  swagger: () => unknown;
};

export async function registerSwaggerDocs(app: FastifyInstance) {
  app.addSchema(healthStatusResponseSchema);
  app.addSchema(errorResponseSchema);
  app.addSchema(validationErrorResponseSchema);
  app.addSchema(authErrorResponseSchema);
  app.addSchema(meUserSchema);
  app.addSchema(meProfileSchema);
  app.addSchema(meResponseSchema);
  app.addSchema(notFoundResponseSchema);
  app.addSchema(savedPlaceParamsSchemaOpenApi);
  app.addSchema(savedCollectionParamsSchemaOpenApi);
  app.addSchema(savedCollectionPlaceParamsSchemaOpenApi);
  app.addSchema(savePlaceBodySchemaOpenApi);
  app.addSchema(savedCollectionBodySchemaOpenApi);
  app.addSchema(updateSavedCollectionBodySchemaOpenApi);
  app.addSchema(addPlaceToCollectionBodySchemaOpenApi);
  app.addSchema(reorderCollectionPlacesBodySchemaOpenApi);
  app.addSchema(savePlaceResponseSchema);
  app.addSchema(unsavePlaceResponseSchema);
  app.addSchema(savedPlaceSummarySchema);
  app.addSchema(savedCollectionSchema);
  app.addSchema(savedCollectionDetailSchema);
  app.addSchema(savedCollectionCompactSchema);
  app.addSchema(savedDashboardResponseSchema);
  app.addSchema(savedCollectionDetailResponseSchema);
  app.addSchema(savedCollectionResponseSchema);
  app.addSchema(deleteCollectionResponseSchema);
  app.addSchema(removePlaceFromCollectionResponseSchema);
  app.addSchema(reorderCollectionPlacesResponseSchema);
  app.addSchema(mapPlacesQuerySchemaOpenApi);
  app.addSchema(mapPlaceSchema);
  app.addSchema(mapPlacesResponseSchema);

  await app.register(swagger, {
    refResolver: {
      buildLocalReference(json, _baseUri, _fragment, index) {
        return json.$id?.toString() ?? `def-${index}`;
      }
    },
    openapi: {
      info: {
        title: "Sloco Backend API",
        version: "0.1.0",
        description: "Taste-based city discovery backend API."
      },
      servers: [
        {
          url: "http://127.0.0.1:3000",
          description: "Local development"
        },
        {
          url: "http://65.108.142.55",
          description: "Production"
        }
      ],
      components: {
        securitySchemes: {
          bearerAuth: {
            type: "http",
            scheme: "bearer",
            bearerFormat: "JWT"
          }
        }
      },
      tags: [
        {
          name: "Health",
          description: "Backend and dependency health checks."
        },
        {
          name: "Me",
          description: "Authenticated user endpoints."
        },
        {
          name: "Map",
          description: "Map discovery endpoints used by the iOS app."
        },
        {
          name: "SavedPlaces",
          description: "Authenticated saved places endpoints."
        }
      ]
    }
  });

  await app.register(swaggerUi, {
    routePrefix: VersionedAppRoute.swaggerDocs,
    uiConfig: {
      deepLinking: true,
      docExpansion: "list"
    },
    staticCSP: false
  });

  app.get(
    VersionedAppRoute.swaggerOpenApiJson,
    {
      schema: {
        hide: true
      }
    },
    async () => (app as AppWithSwagger).swagger()
  );
}
