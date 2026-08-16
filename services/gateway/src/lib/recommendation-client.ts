import { env } from "../config/env.js";
import { measureDependencyMetric } from "../observability/metrics.js";

export type RecommendationHealthResponse = {
  status: "ok";
  service: string;
  environment: "development" | "test" | "production";
};

export type PersonalizedRecommendationRequest = {
  user_id: string;
  favourites_place_ids: string[];
  want_to_go_place_ids: string[];
  dislike_place_ids: string[];
  hide_place_ids: string[];
  limit: number;
  exclude_input_places: boolean;
  debug: boolean;
};

// Serving-receipt fields (request_id, position, profile_id, score_components,
// weights_preset, fallback_used, profiles_count) are optional: an older
// rec-service without them must keep working during deploy skew.
export type PersonalizedRecommendationResponse = {
  user_id: string | null;
  request_id?: string;
  algorithm_version: string;
  embedding_run_id: string;
  weights_preset?: string | null;
  fallback_used?: boolean;
  input_summary: {
    favourites_count: number;
    want_to_go_count: number;
    valid_input_count: number;
    invalid_place_ids: string[];
    candidate_count?: number;
    profiles_count?: number;
  };
  recommendations: Array<{
    rank: number;
    place_id: string;
    score: number;
    similarity?: number | null;
    position?: number;
    profile_id?: number | null;
    score_components?: Record<string, unknown> | null;
  }>;
};

export type RecommendationClient = {
  health: () => Promise<RecommendationHealthResponse>;
  personalizedPlaces: (
    request: PersonalizedRecommendationRequest
  ) => Promise<PersonalizedRecommendationResponse>;
};

const DEFAULT_TIMEOUT_MS = 1500;
const PERSONALIZED_TIMEOUT_MS = 5000;

export class RecommendationServiceNotConfiguredError extends Error {
  constructor() {
    super("Recommendation service URL is not configured");
    this.name = "RecommendationServiceNotConfiguredError";
  }
}

export class RecommendationServiceError extends Error {
  constructor(
    message: string,
    readonly statusCode?: number
  ) {
    super(message);
    this.name = "RecommendationServiceError";
  }
}

export function createRecommendationClient(
  baseUrl = env.RECOMMENDATION_SERVICE_URL,
  timeoutMs = DEFAULT_TIMEOUT_MS
): RecommendationClient {
  return {
    health: () =>
      measureDependencyMetric(
        {
          dependency: "ml-service",
          operation: "http",
          name: "recommendation_health"
        },
        () =>
          requestRecommendationService<RecommendationHealthResponse>(
            baseUrl,
            "/v1/health/ready",
            { timeoutMs }
          )
      ),
    personalizedPlaces: (request) =>
      measureDependencyMetric(
        {
          dependency: "ml-service",
          operation: "http",
          name: "personalized_recommendations"
        },
        () =>
          requestRecommendationService<PersonalizedRecommendationResponse>(
            baseUrl,
            "/v1/recommendations/personalized",
            {
              method: "POST",
              body: request,
              timeoutMs: Math.max(timeoutMs, PERSONALIZED_TIMEOUT_MS)
            }
          ),
        (result) => result.recommendations.length
      )
  };
}

export const recommendationClient = createRecommendationClient();

async function requestRecommendationService<TResponse>(
  baseUrl: string | undefined,
  path: string,
  options: {
    method?: "GET" | "POST";
    body?: unknown;
    timeoutMs: number;
  }
): Promise<TResponse> {
  if (!baseUrl) {
    throw new RecommendationServiceNotConfiguredError();
  }

  const url = new URL(path, baseUrl);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeoutMs);

  try {
    const response = await fetch(url, {
      method: options.method ?? "GET",
      headers:
        options.body === undefined
          ? undefined
          : {
              "content-type": "application/json"
            },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: controller.signal
    });

    if (!response.ok) {
      throw new RecommendationServiceError(
        `Recommendation service request failed with status ${response.status}`,
        response.status
      );
    }

    return (await response.json()) as TResponse;
  } catch (error) {
    if (error instanceof RecommendationServiceError) {
      throw error;
    }

    throw new RecommendationServiceError(
      error instanceof Error
        ? error.message
        : "Recommendation service request failed"
    );
  } finally {
    clearTimeout(timeout);
  }
}
