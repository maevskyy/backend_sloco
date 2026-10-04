import { describe, expect, it } from "vitest";
import { FixedWindowRateLimiter } from "./rate-limiter.js";

describe("FixedWindowRateLimiter", () => {
  it("passes a key up to every rule's limit and says when to come back", () => {
    let now = 0;
    const limiter = new FixedWindowRateLimiter(
      [
        { limit: 2, windowMs: 60_000 },
        { limit: 3, windowMs: 3_600_000 }
      ],
      () => now
    );

    expect(limiter.check("a")).toEqual({ allowed: true });
    expect(limiter.check("a")).toEqual({ allowed: true });
    now = 10_000;
    expect(limiter.check("a")).toEqual({ allowed: false, retryAfterSeconds: 50 });

    // Another key has its own counters.
    expect(limiter.check("b")).toEqual({ allowed: true });

    // The minute window resets; the hour window still has one call left.
    now = 60_000;
    expect(limiter.check("a")).toEqual({ allowed: true });
    expect(limiter.check("a")).toEqual({ allowed: false, retryAfterSeconds: 3540 });
  });

  it("does not count a refused call", () => {
    let now = 0;
    const limiter = new FixedWindowRateLimiter([{ limit: 1, windowMs: 1_000 }], () => now);

    limiter.check("a");
    limiter.check("a");
    limiter.check("a");
    now = 1_000;

    expect(limiter.check("a")).toEqual({ allowed: true });
  });
});
