import assert from "node:assert/strict";
import test from "node:test";

import { parseTraceSession, traceForest, traceLanes } from "../src/lib/trace.ts";

function event(id, kind, parentId = null) {
  return {
    id,
    parent_id: parentId,
    kind,
    label: id,
    start_ms: 0,
    duration_ms: null,
    status: null,
    is_error: false,
    fields: [],
    evidence: {},
  };
}

function run(id, parentRunId, events) {
  return {
    id,
    parent_run_id: parentRunId,
    user_message_id: null,
    title: null,
    started_at: "2026-11-28T01:02:03Z",
    duration_ms: 120,
    status: "completed",
    events,
  };
}

test("an external source uses the same run hierarchy and nearest-agent lanes", () => {
  const session = parseTraceSession({
    schema_version: 1,
    source: { kind: "pi", id: "local-instance" },
    session_id: "third-party",
    recording: true,
    runs: [
      run("first", null, [
        event("parent-tool", "tool"),
        event("child", "agent", "parent-tool"),
        event("child-tool", "tool", "child"),
        event("nested", "agent", "child-tool"),
        event("answer", "message", "nested"),
        event("waiting", "wait"),
      ]),
      run("resumed", "first", []),
    ],
  });

  assert.equal(session.source.kind, "pi");
  const [first] = traceForest(session.runs, (entry) => entry.parent_run_id);
  assert.deepEqual(first.children.map(({ item }) => item.id), ["resumed"]);
  assert.deepEqual(
    traceLanes(traceForest(first.item.events, (entry) => entry.parent_id)).map((lane) => [
      lane.agent?.id ?? null, lane.events.map((item) => item.id),
    ]),
    [[null, ["parent-tool", "waiting"]], ["child", ["child-tool"]], ["nested", ["answer"]]],
  );
});

test("an incomplete or cyclic parent graph keeps every recorded event reachable once", () => {
  const items = [
    event("orphan", "message", "missing"),
    event("a", "tool", "b"),
    event("b", "agent", "a"),
  ];
  function flatten(branch) {
    return [branch.item.id, ...branch.children.flatMap(flatten)];
  }
  assert.deepEqual(traceForest(items, (entry) => entry.parent_id).flatMap(flatten), ["orphan", "a", "b"]);
});

test("the response boundary rejects invalid times and duplicate event identities", () => {
  const base = {
    schema_version: 1,
    source: { kind: "future", id: "source" },
    session_id: "example",
    recording: true,
  };
  assert.throws(() => parseTraceSession({ ...base, runs: [run("a", null, [event("same", "tool"), event("same", "agent")])] }));
  assert.throws(() => parseTraceSession({ ...base, runs: [run("a", null, [{ ...event("model", "model"), start_ms: -1 }])] }));
});
