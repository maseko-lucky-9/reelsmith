/// <reference types="node" />
/**
 * Vendored OpenCut code keeps its MIT notice (spec 017 FR-022, FR-023):
 * every file under `opencut/` names its upstream file and revision, the
 * licence text sits beside it, and none of OpenCut's media-processing or
 * Radix/Next dependencies came along.
 */
import { describe, expect, it } from "vitest";
import license from "./LICENSE-OpenCut?raw";
import packageJson from "../../package.json?raw";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const vendorMd = readFileSync(
  resolve(process.cwd(), "..", "VENDOR.md"),
  "utf8",
);

const OPENCUT_REVISION = "3037a7e";

const vendored = import.meta.glob("./opencut/**/*.{ts,tsx}", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

const sources = Object.entries(vendored).filter(
  ([path]) => !/\.test\.tsx?$/.test(path),
);

const HEADER =
  /^\/\/ (Copied|Adapted) from OpenCut \(MIT\) apps\/web\/src\/(\S+) @ (\w+)/;

describe("vendored OpenCut files", () => {
  it("finds the copied files", () => {
    expect(sources.length).toBeGreaterThanOrEqual(9);
  });

  it.each(sources)(
    "%s starts with the OpenCut origin header",
    (_path, text) => {
      const match = HEADER.exec(text);
      expect(
        match,
        'first line must be "// Copied|Adapted from OpenCut (MIT) apps/web/src/<path> @ 3037a7e"',
      ).not.toBeNull();
      expect(match?.[2]).toMatch(/\.(ts|tsx)$/);
      expect(match?.[3]).toBe(OPENCUT_REVISION);
    },
  );
});

describe("LICENSE-OpenCut", () => {
  it("holds the MIT grant and the copyright line", () => {
    expect(license).toContain("Copyright 2025 OpenCut");
    expect(license).toContain(
      "Permission is hereby granted, free of charge, to any person obtaining a copy",
    );
    expect(license).toContain(
      "The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.",
    );
  });
});

describe("web dependencies", () => {
  const pkg = JSON.parse(packageJson) as {
    dependencies?: Record<string, string>;
    devDependencies?: Record<string, string>;
  };
  const names = Object.keys({ ...pkg.dependencies, ...pkg.devDependencies });

  it.each([
    "wavesurfer.js",
    "@ffmpeg/ffmpeg",
    "@ffmpeg/core",
    "radix-ui",
    "next",
  ])(
    "does not depend on %s (the server owns media; Base UI, not Radix)",
    (name) => {
      expect(names).not.toContain(name);
    },
  );

  it("depends on zustand (the ported stores)", () => {
    expect(names).toContain("zustand");
  });
});

describe("VENDOR.md OpenCut entry", () => {
  const entry = (() => {
    const start = vendorMd.indexOf("## maseko-lucky-9/OpenCut");
    expect(start).toBeGreaterThanOrEqual(0);
    const rest = vendorMd.slice(start + 1);
    const next = rest.search(/\n## /);
    return next < 0 ? rest : rest.slice(0, next);
  })();

  it.each(sources.map(([path]) => path.replace("./opencut/", "")))(
    "names %s",
    (name) => {
      expect(entry).toContain(`\`${name}\``);
    },
  );
});
