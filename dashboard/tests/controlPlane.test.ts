import { afterEach, describe, expect, it, vi } from "vitest";
import { controlPlane } from "../src/lib/controlPlane";

describe("controlPlane wrapper", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    delete process.env.CONTROL_PLANE_URL;
    delete process.env.OPERATOR_TOKEN;
  });

  it("attaches bearer token", async () => {
    process.env.CONTROL_PLANE_URL = "http://cp.test";
    process.env.OPERATOR_TOKEN = "tok";
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response("ok", { status: 200 }));
    await controlPlane.get("/strategies");
    const call = fetchSpy.mock.calls[0];
    expect(call[0]).toBe("http://cp.test/strategies");
    const headers = call[1]!.headers as Record<string, string>;
    expect(headers["authorization"]).toBe("Bearer tok");
  });
});
