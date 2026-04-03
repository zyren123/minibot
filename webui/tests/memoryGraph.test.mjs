import assert from "node:assert/strict";
import test from "node:test";

import { buildGraphLayout } from "../src/views/memoryGraph.ts";

test("buildGraphLayout turns a tree into stable nodes and edges", () => {
  const tree = [
    {
      uri: "memory://characters",
      title: "Characters",
      kind: "folder",
      node_type: null,
      children: [
        {
          uri: "memory://characters/ali",
          title: "Ali",
          kind: "memory",
          node_type: "character",
          children: [],
        },
        {
          uri: "memory://characters/ren",
          title: "Ren",
          kind: "memory",
          node_type: "character",
          children: [],
        },
      ],
    },
  ];

  const layout = buildGraphLayout(tree);

  assert.equal(layout.nodes.length, 3);
  assert.deepEqual(layout.edges[0], { source: "memory://characters", target: "memory://characters/ali" });
  assert.equal(layout.nodes[0].x, 0);
  assert.equal(layout.nodes[1].y, 1);
});
