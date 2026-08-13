import swagger from "@fastify/swagger";
import swaggerUi from "@fastify/swagger-ui";
import type { FastifyInstance } from "fastify";
import { citiesComponentSchemas } from "../modules/cities/index.js";
import { feedComponentSchemas } from "../modules/feed/index.js";
import { healthComponentSchemas } from "../modules/health/index.js";
import { mapComponentSchemas } from "../modules/map/index.js";
import { meComponentSchemas } from "../modules/me/index.js";
import { onboardingComponentSchemas } from "../modules/onboarding/index.js";
import { placesComponentSchemas } from "../modules/places/index.js";
import { reactionsComponentSchemas } from "../modules/reactions/index.js";
import { savedPlacesComponentSchemas } from "../modules/saved-places/index.js";
import { searchComponentSchemas } from "../modules/search/index.js";
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

  for (const schema of onboardingComponentSchemas) {
    app.addSchema(schema);
  }

  for (const schema of reactionsComponentSchemas) {
    app.addSchema(schema);
  }

  for (const schema of savedPlacesComponentSchemas) {
    app.addSchema(schema);
  }

  for (const schema of placesComponentSchemas) {
    app.addSchema(schema);
  }

  for (const schema of mapComponentSchemas) {
    app.addSchema(schema);
  }

  for (const schema of searchComponentSchemas) {
    app.addSchema(schema);
  }

  for (const schema of feedComponentSchemas) {
    app.addSchema(schema);
  }

  for (const schema of citiesComponentSchemas) {
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
          name: "Onboarding",
          description: "Authenticated onboarding-flow endpoints."
        },
        {
          name: "Map",
          description: "Map discovery endpoints used by the iOS app."
        },
        {
          name: "Reactions",
          description: "Authenticated place reaction endpoints."
        },
        {
          name: "Places",
          description: "Place detail read endpoints."
        },
        {
          name: "SavedPlaces",
          description: "Authenticated saved places endpoints."
        },
        {
          name: "Search",
          description: "Global place search endpoints."
        },
        {
          name: "Feed",
          description:
            "Ranked place feed endpoints for Decide for me experiences."
        },
        {
          name: "Cities",
          description: "Catalog cities that have places in the database."
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
