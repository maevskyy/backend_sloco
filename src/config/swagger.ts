import swagger from "@fastify/swagger";
import swaggerUi from "@fastify/swagger-ui";
import type { FastifyInstance } from "fastify";
import { healthComponentSchemas } from "../modules/health/index.js";
import { mapComponentSchemas } from "../modules/map/index.js";
import { meComponentSchemas } from "../modules/me/index.js";
import { savedPlacesComponentSchemas } from "../modules/saved-places/index.js";
import { httpErrorComponentSchemas } from "./http-schemas.js";
import { VersionedAppRoute } from "./routes.js";

type AppWithSwagger = FastifyInstance & {
  swagger: () => unknown;
};

export async function registerSwaggerDocs(app: FastifyInstance) {
  for (const schema of httpErrorComponentSchemas) {
    app.addSchema(schema);
  }

  for (const schema of healthComponentSchemas) {
    app.addSchema(schema);
  }

  for (const schema of meComponentSchemas) {
    app.addSchema(schema);
  }

  for (const schema of savedPlacesComponentSchemas) {
    app.addSchema(schema);
  }

  for (const schema of mapComponentSchemas) {
    app.addSchema(schema);
  }

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
          url: "https://sloco.pp.ua",
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
