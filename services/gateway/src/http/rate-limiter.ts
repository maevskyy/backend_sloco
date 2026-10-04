export type RateLimitRule = {
  limit: number;
  windowMs: number;
};

export type RateLimitDecision =
  | { allowed: true }
  | { allowed: false; retryAfterSeconds: number };

type Window = { startedAt: number; count: number };

// Bound on tracked windows: past it, expired ones are swept on the next check.
const SWEEP_THRESHOLD = 10_000;

/**
 * In-process fixed-window rate limiter: a key passes when it is under every
 * rule's limit (e.g. 6 per minute AND 60 per hour). State lives in this
 * process — with N gateway replicas a key effectively gets N× the limit, and a
 * restart resets the counters; good enough to stop a runaway client.
 */
export class FixedWindowRateLimiter {
  private readonly windows = new Map<string, Window>();

  constructor(
    private readonly rules: readonly RateLimitRule[],
    private readonly now: () => number = Date.now
  ) {}

  check(key: string): RateLimitDecision {
    const now = this.now();

    if (this.windows.size > SWEEP_THRESHOLD) {
      this.sweep(now);
    }

    const windows = this.rules.map((rule, index) => {
      const windowKey = `${index}:${key}`;
      let window = this.windows.get(windowKey);

      if (!window || now - window.startedAt >= rule.windowMs) {
        window = { startedAt: now, count: 0 };
        this.windows.set(windowKey, window);
      }

      return { rule, window };
    });

    const blocked = windows.filter(
      ({ rule, window }) => window.count >= rule.limit
    );

    if (blocked.length > 0) {
      const retryAfterMs = Math.max(
        ...blocked.map(
          ({ rule, window }) => window.startedAt + rule.windowMs - now
        )
      );
      return {
        allowed: false,
        retryAfterSeconds: Math.max(1, Math.ceil(retryAfterMs / 1000))
      };
    }

    for (const { window } of windows) {
      window.count += 1;
    }

    return { allowed: true };
  }

  private sweep(now: number) {
    const longest = Math.max(...this.rules.map((rule) => rule.windowMs));

    for (const [key, window] of this.windows) {
      if (now - window.startedAt >= longest) {
        this.windows.delete(key);
      }
    }
  }
}
