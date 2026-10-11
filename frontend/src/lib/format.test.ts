import { describe, expect, it } from "vitest";

import { bytes, duration, parseTs, pct, timeAgo, titleCase, truncate, usd } from "./format";

describe("format", () => {
  it("treats naive backend timestamps as UTC", () => {
    expect(parseTs("2026-10-11T12:00:00")?.toISOString()).toBe("2026-10-11T12:00:00.000Z");
    expect(parseTs("2026-10-11T12:00:00+02:00")?.toISOString()).toBe("2026-10-11T10:00:00.000Z");
    expect(parseTs(null)).toBeNull();
  });

  it("formats relative times", () => {
    const now = Date.parse("2026-10-11T12:00:00Z");
    expect(timeAgo("2026-10-11T11:59:58", now)).toBe("just now");
    expect(timeAgo("2026-10-11T11:59:30", now)).toBe("30s ago");
    expect(timeAgo("2026-10-11T11:45:00", now)).toBe("15m ago");
    expect(timeAgo("2026-10-11T09:00:00", now)).toBe("3h ago");
    expect(timeAgo(undefined, now)).toBe("—");
  });

  it("formats durations without rolling seconds up to 60", () => {
    const start = "2026-10-11T12:00:00";
    const at = (s: number) => Date.parse("2026-10-11T12:00:00Z") + s * 1000;
    expect(duration(start, null, at(0.25))).toBe("250ms");
    expect(duration(start, null, at(12.34))).toBe("12.3s");
    expect(duration(start, null, at(119.7))).toBe("1m 59s");
    expect(duration(start, "2026-10-11T14:05:00")).toBe("2h 5m");
    expect(duration(null, null)).toBe("—");
  });

  it("formats money, percentages, sizes and labels", () => {
    expect(usd(0.01234)).toBe("$0.0123");
    expect(usd(1234.5)).toBe("$1,234.50");
    expect(usd(null)).toBe("$0.0000");
    expect(pct(0.756)).toBe("76%");
    expect(pct(0.756, 1)).toBe("75.6%");
    expect(bytes(512)).toBe("512 B");
    expect(bytes(2048)).toBe("2.0 KB");
    expect(bytes(5 * 1024 ** 2)).toBe("5.0 MB");
    expect(titleCase("require_approval")).toBe("Require Approval");
    expect(titleCase("tool.call")).toBe("Tool Call");
    expect(truncate("abcdef", 4)).toBe("abc…");
    expect(truncate("abc", 4)).toBe("abc");
  });
});
