const bearerSecurity = [
  {
    bearerAuth: []
  }
] as const;

const savedCategoryEnum = [
  "food",
  "cafe",
  "bar",
  "nature",
  "culture",
  "music",
  "other"
] as const;

export const savedPlaceParamsSchemaOpenApi = {
  $id: "SavedPlaceParams",
  title: "SavedPlaceParams",
  type: "object",
  additionalProperties: false,
  required: ["placeId"],
  properties: {
    placeId: {
      type: "integer",
      minimum: 1,
      example: 123
    }
  }
} as const;

export const savedCollectionParamsSchemaOpenApi = {
  $id: "SavedCollectionParams",
  title: "SavedCollectionParams",
  type: "object",
  additionalProperties: false,
  required: ["collectionId"],
  properties: {
    collectionId: {
      type: "string",
      format: "uuid",
      example: "4b572b66-d74d-49bb-b9b5-9780c266c6f7"
    }
  }
} as const;

export const savedCollectionPlaceParamsSchemaOpenApi = {
  $id: "SavedCollectionPlaceParams",
  title: "SavedCollectionPlaceParams",
  type: "object",
  additionalProperties: false,
  required: ["collectionId", "placeId"],
  properties: {
    collectionId: {
      type: "string",
      format: "uuid",
      example: "4b572b66-d74d-49bb-b9b5-9780c266c6f7"
    },
    placeId: {
      type: "integer",
      minimum: 1,
      example: 123
    }
  }
} as const;

export const savePlaceBodySchemaOpenApi = {
  $id: "SavePlaceBody",
  title: "SavePlaceBody",
  type: "object",
  additionalProperties: false,
  required: ["placeId"],
  properties: {
    placeId: {
      type: "integer",
      minimum: 1,
      example: 123
    },
    collectionIds: {
      type: "array",
      items: {
        type: "string",
        format: "uuid"
      },
      example: ["4b572b66-d74d-49bb-b9b5-9780c266c6f7"]
    }
  }
} as const;

export const savedCollectionBodySchemaOpenApi = {
  $id: "SavedCollectionBody",
  title: "SavedCollectionBody",
  type: "object",
  additionalProperties: false,
  required: ["name"],
  properties: {
    name: {
      type: "string",
      minLength: 1,
      maxLength: 80,
      example: "Coffee & work"
    },
    colorHex: {
      type: "string",
      pattern: "^#[0-9a-fA-F]{6}$",
      example: "#e6b15c"
    }
  }
} as const;

export const updateSavedCollectionBodySchemaOpenApi = {
  $id: "UpdateSavedCollectionBody",
  title: "UpdateSavedCollectionBody",
  type: "object",
  additionalProperties: false,
  properties: {
    name: {
      type: "string",
      minLength: 1,
      maxLength: 80,
      example: "Quiet evenings"
    },
    colorHex: {
      type: "string",
      nullable: true,
      pattern: "^#[0-9a-fA-F]{6}$",
      example: "#b98ce8"
    },
    sortOrder: {
      type: "integer",
      minimum: 0,
      example: 2
    }
  }
} as const;

export const addPlaceToCollectionBodySchemaOpenApi = {
  $id: "AddPlaceToCollectionBody",
  title: "AddPlaceToCollectionBody",
  type: "object",
  additionalProperties: false,
  required: ["placeId"],
  properties: {
    placeId: {
      type: "integer",
      minimum: 1,
      example: 123
    }
  }
} as const;

export const reorderCollectionPlacesBodySchemaOpenApi = {
  $id: "ReorderCollectionPlacesBody",
  title: "ReorderCollectionPlacesBody",
  type: "object",
  additionalProperties: false,
  required: ["placeIds"],
  properties: {
    placeIds: {
      type: "array",
      items: {
        type: "integer",
        minimum: 1
      },
      example: [103, 101, 102]
    }
  }
} as const;

export const savePlaceResponseSchema = {
  $id: "SavePlaceResponse",
  title: "SavePlaceResponse",
  type: "object",
  additionalProperties: false,
  required: ["placeId", "isSaved", "collectionIds", "savedAt"],
  properties: {
    placeId: {
      type: "integer",
      example: 123
    },
    isSaved: {
      type: "boolean",
      enum: [true],
      example: true
    },
    collectionIds: {
      type: "array",
      items: {
        type: "string",
        format: "uuid"
      }
    },
    savedAt: {
      type: "string",
      format: "date-time",
      example: "2026-05-31T10:00:00.000Z"
    }
  }
} as const;

export const unsavePlaceResponseSchema = {
  $id: "UnsavePlaceResponse",
  title: "UnsavePlaceResponse",
  type: "object",
  additionalProperties: false,
  required: ["placeId", "isSaved", "collectionIds"],
  properties: {
    placeId: {
      type: "integer",
      example: 123
    },
    isSaved: {
      type: "boolean",
      enum: [false],
      example: false
    },
    collectionIds: {
      type: "array",
      items: {
        type: "string",
        format: "uuid"
      },
      maxItems: 0
    }
  }
} as const;

export const savedPlaceSummarySchema = {
  $id: "SavedPlaceSummary",
  title: "SavedPlaceSummary",
  type: "object",
  additionalProperties: false,
  required: [
    "id",
    "source",
    "sourceId",
    "name",
    "city",
    "country",
    "latitude",
    "longitude",
    "category",
    "categoryLabel",
    "rating",
    "priceLevel",
    "tags",
    "distanceText",
    "imageUrl",
    "savedAt",
    "lastViewedAt"
  ],
  properties: {
    id: {
      type: "integer",
      example: 123
    },
    source: {
      type: "string",
      example: "tripadvisor"
    },
    sourceId: {
      type: "string",
      example: "d5529357"
    },
    name: {
      type: "string",
      example: "Quiet Coffee"
    },
    city: {
      type: "string",
      example: "Berlin"
    },
    country: {
      type: "string",
      example: "Germany"
    },
    latitude: {
      type: "number",
      example: 52.52
    },
    longitude: {
      type: "number",
      example: 13.405
    },
    category: {
      type: "string",
      enum: savedCategoryEnum,
      example: "cafe"
    },
    categoryLabel: {
      type: "string",
      example: "Cafe"
    },
    rating: {
      type: "number",
      nullable: true,
      example: 4.5
    },
    priceLevel: {
      type: "integer",
      nullable: true,
      minimum: 0,
      maximum: 4,
      example: 2
    },
    tags: {
      type: "array",
      items: {
        type: "string"
      },
      example: ["Coffee", "Work"]
    },
    distanceText: {
      type: "string",
      nullable: true,
      example: null
    },
    imageUrl: {
      type: "string",
      nullable: true,
      example: null
    },
    savedAt: {
      type: "string",
      format: "date-time",
      example: "2026-05-31T10:00:00.000Z"
    },
    lastViewedAt: {
      type: "string",
      format: "date-time",
      nullable: true,
      example: null
    }
  }
} as const;

export const savedCollectionSchema = {
  $id: "SavedCollection",
  title: "SavedCollection",
  type: "object",
  additionalProperties: false,
  required: [
    "id",
    "name",
    "colorHex",
    "placeCount",
    "placeIds",
    "previewPlaces",
    "createdAt",
    "updatedAt",
    "isDefault",
    "sortOrder"
  ],
  properties: {
    id: {
      type: "string",
      format: "uuid"
    },
    name: {
      type: "string",
      example: "Want to go"
    },
    colorHex: {
      type: "string",
      nullable: true,
      example: "#f0805f"
    },
    placeCount: {
      type: "integer",
      minimum: 0,
      example: 3
    },
    placeIds: {
      type: "array",
      items: {
        type: "integer"
      }
    },
    previewPlaces: {
      type: "array",
      maxItems: 3,
      items: {
        $ref: "SavedPlaceSummary#"
      }
    },
    createdAt: {
      type: "string",
      format: "date-time"
    },
    updatedAt: {
      type: "string",
      format: "date-time"
    },
    isDefault: {
      type: "boolean",
      example: true
    },
    sortOrder: {
      type: "integer",
      example: 0
    }
  }
} as const;

export const savedCollectionDetailSchema = {
  $id: "SavedCollectionDetail",
  title: "SavedCollectionDetail",
  type: "object",
  additionalProperties: false,
  required: [
    "id",
    "name",
    "colorHex",
    "placeCount",
    "placeIds",
    "createdAt",
    "updatedAt",
    "isDefault",
    "sortOrder"
  ],
  properties: {
    ...savedCollectionSchema.properties
  }
} as const;

export const savedCollectionCompactSchema = {
  $id: "SavedCollectionCompact",
  title: "SavedCollectionCompact",
  type: "object",
  additionalProperties: false,
  required: ["id", "name", "colorHex", "placeCount"],
  properties: {
    id: {
      type: "string",
      format: "uuid"
    },
    name: {
      type: "string"
    },
    colorHex: {
      type: "string",
      nullable: true
    },
    placeCount: {
      type: "integer",
      minimum: 0
    }
  }
} as const;

export const savedDashboardResponseSchema = {
  $id: "SavedDashboardResponse",
  title: "SavedDashboardResponse",
  type: "object",
  additionalProperties: false,
  required: ["summary", "collections", "recentlySaved"],
  properties: {
    summary: {
      type: "object",
      additionalProperties: false,
      required: [
        "savedPlaceCount",
        "collectionCount",
        "recommendationsUseSavedPlaces"
      ],
      properties: {
        savedPlaceCount: {
          type: "integer",
          minimum: 0
        },
        collectionCount: {
          type: "integer",
          minimum: 0
        },
        recommendationsUseSavedPlaces: {
          type: "boolean",
          enum: [true]
        }
      }
    },
    collections: {
      type: "array",
      items: {
        $ref: "SavedCollection#"
      }
    },
    recentlySaved: {
      type: "array",
      items: {
        $ref: "SavedPlaceSummary#"
      }
    }
  }
} as const;

export const savedCollectionDetailResponseSchema = {
  $id: "SavedCollectionDetailResponse",
  title: "SavedCollectionDetailResponse",
  type: "object",
  additionalProperties: false,
  required: ["collection", "places", "availableCollections"],
  properties: {
    collection: {
      $ref: "SavedCollectionDetail#"
    },
    places: {
      type: "array",
      items: {
        $ref: "SavedPlaceSummary#"
      }
    },
    availableCollections: {
      type: "array",
      items: {
        $ref: "SavedCollectionCompact#"
      }
    }
  }
} as const;

export const savedCollectionResponseSchema = {
  $id: "SavedCollectionResponse",
  title: "SavedCollectionResponse",
  type: "object",
  additionalProperties: false,
  required: ["collection"],
  properties: {
    collection: {
      $ref: "SavedCollection#"
    }
  }
} as const;

export const deleteCollectionResponseSchema = {
  $id: "DeleteSavedCollectionResponse",
  title: "DeleteSavedCollectionResponse",
  type: "object",
  additionalProperties: false,
  required: ["collectionId", "deleted"],
  properties: {
    collectionId: {
      type: "string",
      format: "uuid"
    },
    deleted: {
      type: "boolean",
      enum: [true]
    }
  }
} as const;

export const removePlaceFromCollectionResponseSchema = {
  $id: "RemovePlaceFromCollectionResponse",
  title: "RemovePlaceFromCollectionResponse",
  type: "object",
  additionalProperties: false,
  required: ["collectionId", "placeId", "removed"],
  properties: {
    collectionId: {
      type: "string",
      format: "uuid"
    },
    placeId: {
      type: "integer"
    },
    removed: {
      type: "boolean",
      enum: [true]
    }
  }
} as const;

export const reorderCollectionPlacesResponseSchema = {
  $id: "ReorderCollectionPlacesResponse",
  title: "ReorderCollectionPlacesResponse",
  type: "object",
  additionalProperties: false,
  required: ["collectionId", "placeIds"],
  properties: {
    collectionId: {
      type: "string",
      format: "uuid"
    },
    placeIds: {
      type: "array",
      items: {
        type: "integer"
      }
    }
  }
} as const;

export const notFoundResponseSchema = {
  $id: "NotFoundResponse",
  title: "NotFoundResponse",
  type: "object",
  additionalProperties: false,
  required: ["status", "message"],
  properties: {
    status: {
      type: "string",
      enum: ["error"]
    },
    message: {
      type: "string",
      example: "Place not found"
    }
  }
} as const;

const savedPlacesErrorResponses = {
  400: {
    $ref: "ValidationErrorResponse#"
  },
  401: {
    $ref: "AuthErrorResponse#"
  },
  404: {
    $ref: "NotFoundResponse#"
  },
  409: {
    $ref: "ErrorResponse#"
  },
  500: {
    $ref: "ErrorResponse#"
  }
} as const;

export const getSavedDashboardRouteSchema = {
  tags: ["SavedPlaces"],
  summary: "Get saved dashboard.",
  description:
    "Returns summary, collections, and recently saved places for the authenticated user.",
  security: bearerSecurity,
  response: {
    200: {
      $ref: "SavedDashboardResponse#"
    },
    ...savedPlacesErrorResponses
  }
} as const;

export const getSavedCollectionRouteSchema = {
  tags: ["SavedPlaces"],
  summary: "Get saved collection detail.",
  security: bearerSecurity,
  params: {
    $ref: "SavedCollectionParams#"
  },
  response: {
    200: {
      $ref: "SavedCollectionDetailResponse#"
    },
    ...savedPlacesErrorResponses
  }
} as const;

export const savePlaceRouteSchema = {
  tags: ["SavedPlaces"],
  summary: "Save a place.",
  security: bearerSecurity,
  body: {
    $ref: "SavePlaceBody#"
  },
  response: {
    200: {
      $ref: "SavePlaceResponse#"
    },
    ...savedPlacesErrorResponses
  }
} as const;

export const unsavePlaceRouteSchema = {
  tags: ["SavedPlaces"],
  summary: "Unsave a place.",
  security: bearerSecurity,
  params: {
    $ref: "SavedPlaceParams#"
  },
  response: {
    200: {
      $ref: "UnsavePlaceResponse#"
    },
    ...savedPlacesErrorResponses
  }
} as const;

export const createCollectionRouteSchema = {
  tags: ["SavedPlaces"],
  summary: "Create saved collection.",
  security: bearerSecurity,
  body: {
    $ref: "SavedCollectionBody#"
  },
  response: {
    200: {
      $ref: "SavedCollectionResponse#"
    },
    ...savedPlacesErrorResponses
  }
} as const;

export const updateCollectionRouteSchema = {
  tags: ["SavedPlaces"],
  summary: "Update saved collection.",
  security: bearerSecurity,
  params: {
    $ref: "SavedCollectionParams#"
  },
  body: {
    $ref: "UpdateSavedCollectionBody#"
  },
  response: {
    200: {
      $ref: "SavedCollectionResponse#"
    },
    ...savedPlacesErrorResponses
  }
} as const;

export const deleteCollectionRouteSchema = {
  tags: ["SavedPlaces"],
  summary: "Delete saved collection.",
  security: bearerSecurity,
  params: {
    $ref: "SavedCollectionParams#"
  },
  response: {
    200: {
      $ref: "DeleteSavedCollectionResponse#"
    },
    ...savedPlacesErrorResponses
  }
} as const;

export const addPlaceToCollectionRouteSchema = {
  tags: ["SavedPlaces"],
  summary: "Add place to saved collection.",
  security: bearerSecurity,
  params: {
    $ref: "SavedCollectionParams#"
  },
  body: {
    $ref: "AddPlaceToCollectionBody#"
  },
  response: {
    200: {
      $ref: "SavePlaceResponse#"
    },
    ...savedPlacesErrorResponses
  }
} as const;

export const removePlaceFromCollectionRouteSchema = {
  tags: ["SavedPlaces"],
  summary: "Remove place from saved collection.",
  security: bearerSecurity,
  params: {
    $ref: "SavedCollectionPlaceParams#"
  },
  response: {
    200: {
      $ref: "RemovePlaceFromCollectionResponse#"
    },
    ...savedPlacesErrorResponses
  }
} as const;

export const reorderCollectionPlacesRouteSchema = {
  tags: ["SavedPlaces"],
  summary: "Reorder places in saved collection.",
  security: bearerSecurity,
  params: {
    $ref: "SavedCollectionParams#"
  },
  body: {
    $ref: "ReorderCollectionPlacesBody#"
  },
  response: {
    200: {
      $ref: "ReorderCollectionPlacesResponse#"
    },
    ...savedPlacesErrorResponses
  }
} as const;
