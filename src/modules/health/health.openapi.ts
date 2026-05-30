export const healthStatusResponseSchema = {
  $id: "HealthStatusResponse",
  title: "HealthStatusResponse",
  type: "object",
  additionalProperties: false,
  required: ["status"],
  properties: {
    status: {
      type: "string",
      enum: ["ok"],
      description: "Health check status."
    }
  },
  example: {
    status: "ok"
  }
} as const;

export const errorResponseSchema = {
  $id: "ErrorResponse",
  title: "ErrorResponse",
  type: "object",
  additionalProperties: false,
  required: ["status"],
  properties: {
    status: {
      type: "string",
      enum: ["error"],
      description: "Error status."
    }
  },
  example: {
    status: "error"
  }
} as const;

export const healthRouteSchema = {
  tags: ["Health"],
  summary: "Check backend health.",
  description: "Returns ok when the backend process is running.",
  response: {
    200: {
      $ref: "HealthStatusResponse#"
    }
  }
} as const;

export const supabaseHealthRouteSchema = {
  tags: ["Health"],
  summary: "Check Supabase health.",
  description: "Returns ok when the backend can read from Supabase.",
  response: {
    200: {
      $ref: "HealthStatusResponse#"
    },
    500: {
      $ref: "ErrorResponse#"
    }
  }
} as const;
