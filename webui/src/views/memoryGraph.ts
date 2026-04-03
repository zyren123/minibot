export type MemoryTreeNode = {
  uri: string;
  title: string;
  kind: string;
  node_type: string | null;
  children: MemoryTreeNode[];
};

export type MemoryGraphLayoutNode = {
  id: string;
  uri: string;
  title: string;
  kind: string;
  nodeType: string | null;
  x: number;
  y: number;
};

export type MemoryGraphLayoutEdge = {
  source: string;
  target: string;
};

export type MemoryGraphLayout = {
  nodes: MemoryGraphLayoutNode[];
  edges: MemoryGraphLayoutEdge[];
};

export function buildGraphLayout(tree: MemoryTreeNode[]): MemoryGraphLayout {
  const nodes: MemoryGraphLayoutNode[] = [];
  const edges: MemoryGraphLayoutEdge[] = [];
  const depthColumns = new Map<number, number>();

  function visit(items: MemoryTreeNode[], depth: number) {
    for (const item of items) {
      const column = depthColumns.get(depth) ?? 0;
      depthColumns.set(depth, column + 1);
      nodes.push({
        id: item.uri,
        uri: item.uri,
        title: item.title,
        kind: item.kind,
        nodeType: item.node_type,
        x: column,
        y: depth,
      });
      for (const child of item.children) {
        edges.push({ source: item.uri, target: child.uri });
      }
      visit(item.children, depth + 1);
    }
  }

  visit(tree, 0);
  return { nodes, edges };
}
