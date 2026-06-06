import { describe, expect, it } from "vitest";
import { extractBearerToken } from "../auth.service.js";

describe("auth service", () => {
  it("extracts a bearer token", () => {
    expect(extractBearerToken("Bearer token-123")).toBe("token-123");
  });

  it("extracts bearer tokens case-insensitively", () => {
    expect(extractBearerToken("bearer token-123")).toBe("token-123");
  });

  it("returns null for missing or malformed authorization headers", () => {
    expect(extractBearerToken(undefined)).toBeNull();
    expect(extractBearerToken("token-123")).toBeNull();
    expect(extractBearerToken("Bearer ")).toBeNull();
  });
});
