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
  required: ["city", "swLat", "swLng", "neLat", "neLng"],
  properties: {
    city: {
      type: "string",
      minLength: 1,
      description: "City name. MVP supports Berlin.",
      example: "Berlin"
    },
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
      default: 100,
      description: "Max places to return.",
      example: 100
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
    "city",
    "latitude",
    "longitude",
    "rating",
    "priceRange",
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
      enum: ["tripadvisor"],
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
    priceRange: {
      type: "string",
      nullable: true,
      example: "$$ - $$$"
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
        city: "Berlin",
        latitude: 52.552578,
        longitude: 13.352883,
        rating: 4,
        priceRange: "$$ - $$$",
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
