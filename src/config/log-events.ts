export enum LogEventType {
  Metric = "metric",
  Request = "request",
  Response = "response"
}

export enum LogEvent {
  CacheMetric = "cache metric",
  DependencyMetric = "dependency metric",
  HttpRequestMetric = "http request metric",
  RequestCompleted = "request completed",
  ResponseSummary = "response summary"
}

export enum LogMessagePrefix {
  Request = "REQUEST",
  Response = "RESPONSE"
}
