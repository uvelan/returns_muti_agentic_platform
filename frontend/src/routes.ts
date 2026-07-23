import { lazy } from "react";
import { LayoutDashboard, Database, FileCheck2 } from "lucide-react";

export type RouteCapability = 
  | "LIVE"
  | "FIXTURE"
  | "BLOCKED";

export type RouteDefinition = {
  path: string;
  name: string;
  icon?: React.ComponentType<{ size?: number; className?: string }>;
  capability: RouteCapability;
  component: React.LazyExoticComponent<React.ComponentType<unknown>>;
  navigable: boolean;
};

export const routes: RouteDefinition[] = [
  {
    path: "/overview",
    name: "Overview",
    icon: LayoutDashboard,
    capability: "LIVE",
    navigable: true,
    component: lazy(() => import("./features/data-console/pages/OverviewPage").then(m => ({ default: m.OverviewPage }))),
  },
  {
    path: "/data-console/inventory",
    name: "Inventory",
    icon: Database,
    capability: "LIVE",
    navigable: true,
    component: lazy(() => import("./features/data-console/pages/InventoryPage").then(m => ({ default: m.InventoryPage }))),
  },
  {
    path: "/data-console/graph-evidence",
    name: "Graph Evidence",
    icon: FileCheck2,
    capability: "LIVE",
    navigable: true,
    component: lazy(() => import("./features/data-console/pages/GraphEvidencePage").then(m => ({ default: m.GraphEvidencePage }))),
  }
];
