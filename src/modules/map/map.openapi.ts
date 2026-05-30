const validationIssueSchema = {
  type: "object",
  additionalProperties: true,
  properties: {
    code: {
      type: "string"
    },
    path: {
      type: "array",
      items: {
        type: ["string", "number"]
      }
    },
    message: {
      type: "string"
    }
  }
} as const;

export const validationErrorResponseSchema = {
  $id: "ValidationErrorResponse",
  title: "ValidationErrorResponse",
  type: "object",
  additionalProperties: false,
  required: ["status", "message", "issues"],
  properties: {
    status: {
      type: "string",
      enum: ["error"]
    },
    message: {
      type: "string",
      example: "Invalid map places query"
    },
    issues: {
      type: "array",
      items: validationIssueSchema
    }
  },
  example: {
    status: "error",
    message: "Invalid map places query",
    issues: []
  }
} as const;

export const mapPlacesQuerySchemaOpenApi = {
  $id: "MapPlacesQuery",
  title: "MapPlacesQuery",
  type: "object",
  additionalProperties: false,
  required: ["swLat", "swLng", "neLat", "neLng"],
  properties: {
    swLat: {
      type: "number",
      description: "South-west map corner latitude.",
      example: 52.48
    },
    swLng: {
      type: "number",
      description: "South-west map corner longitude.",
      example: 13.33
    },
    neLat: {
      type: "number",
      description: "North-east map corner latitude.",
      example: 52.56
    },
    neLng: {
      type: "number",
      description: "North-east map corner longitude.",
      example: 13.47
    },
    limit: {
      type: "integer",
      minimum: 1,
      maximum: 200,
      description:
        "Optional user/debug cap. Backend still clamps it against zoom-based density.",
      example: 100
    },
    zoom: {
      type: "integer",
      minimum: 1,
      maximum: 22,
      description:
        "Frontend map zoom level. Optional. If omitted, density is derived from the bbox span.",
      example: 13
    }
  }
} as const;

export const mapPlaceSchema = {
  $id: "MapPlace",
  title: "MapPlace",
  type: "object",
  additionalProperties: false,
  required: [
    "id",
    "source",
    "sourceId",
    "name",
    "country",
    "city",
    "latitude",
    "longitude",
    "rating",
    "priceLevel",
    "numberOfReviews",
    "rawCuisineStyle"
  ],
  properties: {
    id: {
      type: "integer",
      example: 1
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
      example: "Pane e Vino"
    },
    country: {
      type: "string",
      example: "Germany"
    },
    city: {
      type: "string",
      example: "Berlin"
    },
    latitude: {
      type: "number",
      example: 52.552578
    },
    longitude: {
      type: "number",
      example: 13.352883
    },
    rating: {
      type: "number",
      nullable: true,
      example: 4
    },
    priceLevel: {
      type: "integer",
      nullable: true,
      minimum: 1,
      maximum: 4,
      example: 2
    },
    numberOfReviews: {
      type: "integer",
      nullable: true,
      example: 17
    },
    rawCuisineStyle: {
      type: "string",
      nullable: true,
      example: "Italian, Pizza, Mediterranean"
    }
  }
} as const;

export const mapPlacesResponseSchema = {
  $id: "MapPlacesResponse",
  title: "MapPlacesResponse",
  type: "object",
  additionalProperties: false,
  required: ["places"],
  properties: {
    places: {
      type: "array",
      items: {
        $ref: "MapPlace#"
      }
    }
  },
  example: {
    places: [
      {
        id: 1,
        source: "tripadvisor",
        sourceId: "d5529357",
        name: "Pane e Vino",
        country: "Germany",
        city: "Berlin",
        latitude: 52.552578,
        longitude: 13.352883,
        rating: 4,
        priceLevel: 2,
        numberOfReviews: 17,
        rawCuisineStyle: null
      }
    ]
  }
} as const;

export const mapPlacesRouteSchema = {
  tags: ["Map"],
  summary: "Get places visible in a map bounding box.",
  description:
    "Used by the iOS map screen when the user opens the map or changes the visible region. The frontend sends the current map viewport as south-west and north-east coordinates. Backend returns lightweight place markers.",
  querystring: {
    $ref: "MapPlacesQuery#"
  },
  response: {
    200: {
      $ref: "MapPlacesResponse#"
    },
    400: {
      $ref: "ValidationErrorResponse#"
    },
    500: {
      $ref: "ErrorResponse#"
    }
  }
} as const;
