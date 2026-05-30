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
import { VersionedAppRoute } from "./routes.js";

type AppWithSwagger = FastifyInstance & {
  swagger: () => unknown;
};

export async function registerSwaggerDocs(app: FastifyInstance) {
  app.addSchema(healthStatusResponseSchema);
  app.addSchema(errorResponseSchema);
  app.addSchema(validationErrorResponseSchema);
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
          url: "http://52.18.13.69",
          description: "Production"
        }
      ],
      tags: [
        {
          name: "Health",
          description: "Backend and dependency health checks."
        },
        {
          name: "Map",
          description: "Map discovery endpoints used by the iOS app."
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
