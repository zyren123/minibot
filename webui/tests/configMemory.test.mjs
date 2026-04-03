import assert from "node:assert/strict";
import test from "node:test";

import {
  buildMemoryConfigUpdate,
  memoryComparable,
  memoryDraftFromConfig,
} from "../src/views/configMemory.ts";

test("memoryDraftFromConfig preserves backend and configured postgres URL", () => {
  const draft = memoryDraftFromConfig({
    base_url: null,
    model: null,
    stream_enabled: true,
    skills_dirs: [],
    user_skills_dir: null,
    project_skills_dir: null,
    default_skill_target: "user",
    available_skill_targets: ["user", "project"],
    tool_plugins: [],
    api_key_masked: null,
    memory_backend: "postgres",
    memory_database_url_configured: true,
    memory_database_url_value: "postgresql://user:pass@host:5432/db",
  });

  assert.deepEqual(draft, {
    backend: "postgres",
    databaseUrlConfigured: true,
    databaseUrlValue: "postgresql://user:pass@host:5432/db",
  });
});

test("buildMemoryConfigUpdate trims URL and allows sqlite to keep a cleared value", () => {
  assert.deepEqual(
    buildMemoryConfigUpdate({
      backend: "postgres",
      databaseUrlConfigured: true,
      databaseUrlValue: "  postgresql://user:pass@host:5432/db  ",
    }),
    {
      memory_backend: "postgres",
      memory_database_url_value: "postgresql://user:pass@host:5432/db",
    },
  );

  assert.deepEqual(
    buildMemoryConfigUpdate({
      backend: "sqlite",
      databaseUrlConfigured: false,
      databaseUrlValue: "   ",
    }),
    {
      memory_backend: "sqlite",
      memory_database_url_value: null,
    },
  );
});

test("memoryComparable reflects backend and url changes", () => {
  const a = memoryComparable({
    backend: "sqlite",
    databaseUrlConfigured: false,
    databaseUrlValue: "",
  });
  const b = memoryComparable({
    backend: "postgres",
    databaseUrlConfigured: true,
    databaseUrlValue: "postgresql://host/db",
  });

  assert.notEqual(a, b);
});
