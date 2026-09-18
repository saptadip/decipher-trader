import { describe, expect, it } from "vitest";
import { hashPassword, verifyPassword } from "../src/lib/session";

describe("session helpers", () => {
  it("hash then verify succeeds", async () => {
    const hash = await hashPassword("hunter2");
    expect(await verifyPassword("hunter2", hash)).toBe(true);
  });

  it("wrong password fails", async () => {
    const hash = await hashPassword("hunter2");
    expect(await verifyPassword("wrong", hash)).toBe(false);
  });
});
