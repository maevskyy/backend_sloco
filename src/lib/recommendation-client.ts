import { env } from "../config/env.js";

export type RecommendationHealthResponse = {
  status: "ok";
  service: string;
  environment: "development" | "test" | "production";
};

export type RecommendationClient = {
  health: () => Promise<RecommendationHealthResponse>;
};

const DEFAULT_TIMEOUT_MS = 1500;

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
      requestRecommendationService<RecommendationHealthResponse>(
        baseUrl,
        "/v1/health/ready",
        timeoutMs
      )
  };
}

export const recommendationClient = createRecommendationClient();

async function requestRecommendationService<TResponse>(
  baseUrl: string | undefined,
  path: string,
  timeoutMs: number
): Promise<TResponse> {
  if (!baseUrl) {
    throw new RecommendationServiceNotConfiguredError();
  }

  const url = new URL(path, baseUrl);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(url, { signal: controller.signal });

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
