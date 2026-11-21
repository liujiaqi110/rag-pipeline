import type { DepartmentOut, UserOut } from '@/api/types'

export interface DeptNode extends DepartmentOut { label: string; count: number; children: DeptNode[] }

/** Build the department tree from parent links; unknown parents become roots (never dropped). */
export function buildDeptTree(list: DepartmentOut[], users: UserOut[]): DeptNode[] {
  const nodes = new Map<string, DeptNode>(list.map((d) => [d.name, { ...d, label: d.name, count: users.filter((u) => u.departments.includes(d.name)).length, children: [] }]))
  const roots: DeptNode[] = []
  for (const n of nodes.values()) (n.parent && nodes.has(n.parent) && n.parent !== n.name ? nodes.get(n.parent)!.children : roots).push(n)
  return roots
}
