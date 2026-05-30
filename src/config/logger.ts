import type {
  FastifyReply,
  FastifyRequest,
  FastifyServerOptions
} from "fastify";

type NodeEnv = "development" | "test" | "production";

type LoggerConfig = Pick<
  FastifyServerOptions,
  "logger" | "disableRequestLogging"
>;

const levelFormatter = (label: string) => ({
  level: label
});

export function createLoggerConfig(nodeEnv: NodeEnv): LoggerConfig {
  if (nodeEnv === "test") {
    return {
      logger: false,
      disableRequestLogging: true
    };
  }

  if (nodeEnv === "development") {
    return {
      logger: {
        level: "debug",
        formatters: {
          level: levelFormatter
        },
        transport: {
          target: "pino-pretty",
          options: {
            colorize: true,
            ignore: "pid,hostname",
            translateTime: "HH:MM:ss.l"
          }
        }
      },
      disableRequestLogging: true
    };
  }

  return {
    logger: {
      level: "info",
      formatters: {
        level: levelFormatter
      }
    },
    disableRequestLogging: true
  };
}

export function logRequestCompletion(
  request: FastifyRequest,
  reply: FastifyReply
) {
  if (!request.log) {
    return;
  }

  const path = getRequestPath(request.url);
  const statusCode = reply.statusCode;
  const responseTimeMs = Math.round(reply.elapsedTime);
  const level = getRequestLogLevel(statusCode);
  const message = `REQUEST ${request.method} ${path} ${statusCode} ${responseTimeMs}ms`;

  request.log[level](
    {
      eventType: "request",
      event: "request completed",
      method: request.method,
      url: request.url,
      path,
      statusCode,
      responseTimeMs
    },
    message
  );
}

function getRequestPath(url: string) {
  return url.split("?")[0] || url;
}

function getRequestLogLevel(statusCode: number): "info" | "warn" | "error" {
  if (statusCode >= 500) {
    return "error";
  }

  if (statusCode >= 400) {
    return "warn";
  }

  return "info";
}
