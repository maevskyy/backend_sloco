export const authErrorResponseSchema = {
  $id: "AuthErrorResponse",
  title: "AuthErrorResponse",
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
      enum: ["Unauthorized"]
    }
  },
  example: {
    status: "error",
    message: "Unauthorized"
  }
} as const;

export const meUserSchema = {
  $id: "MeUser",
  title: "MeUser",
  type: "object",
  additionalProperties: false,
  required: ["id", "email"],
  properties: {
    id: {
      type: "string",
      format: "uuid",
      example: "0f70a78a-05f8-45da-81b5-a435fdadf16c"
    },
    email: {
      type: "string",
      nullable: true,
      example: "user@example.com"
    }
  }
} as const;

export const meProfileSchema = {
  $id: "MeProfile",
  title: "MeProfile",
  type: "object",
  additionalProperties: false,
  required: ["userId", "displayName", "onboardingStatus"],
  properties: {
    userId: {
      type: "string",
      format: "uuid",
      example: "0f70a78a-05f8-45da-81b5-a435fdadf16c"
    },
    displayName: {
      type: "string",
      nullable: true,
      example: null
    },
    onboardingStatus: {
      type: "string",
      example: "not_started"
    }
  }
} as const;

export const meResponseSchema = {
  $id: "MeResponse",
  title: "MeResponse",
  type: "object",
  additionalProperties: false,
  required: ["user", "profile"],
  properties: {
    user: {
      $ref: "MeUser#"
    },
    profile: {
      $ref: "MeProfile#"
    }
  },
  example: {
    user: {
      id: "0f70a78a-05f8-45da-81b5-a435fdadf16c",
      email: "user@example.com"
    },
    profile: {
      userId: "0f70a78a-05f8-45da-81b5-a435fdadf16c",
      displayName: null,
      onboardingStatus: "not_started"
    }
  }
} as const;

export const meRouteSchema = {
  tags: ["Me"],
  summary: "Get the authenticated user and profile.",
  description:
    "Verifies a Supabase Auth access token and returns the current user with their backend profile.",
  security: [
    {
      bearerAuth: []
    }
  ],
  response: {
    200: {
      $ref: "MeResponse#"
    },
    500: {
      $ref: "ErrorResponse#"
    },
    401: {
      $ref: "AuthErrorResponse#"
    }
  }
} as const;
