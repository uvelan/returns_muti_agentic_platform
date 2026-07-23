import { lazy } from "react";
import { LayoutDashboard, Database, FileCheck2, HardDrive, Search } from "lucide-react";

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
  },
  {
    path: "/data-console/sources",
    name: "Data Sources",
    icon: HardDrive,
    capability: "FIXTURE",
    navigable: true,
    component: lazy(() => import("./features/data-console/pages/SourcesPage").then(m => ({ default: m.SourcesPage }))),
  },
  {
    path: "/data-console/sources/:sourceId",
    name: "Source Detail",
    capability: "FIXTURE",
    navigable: false,
    component: lazy(() => import("./features/data-console/pages/SourceDetailPage").then(m => ({ default: m.SourceDetailPage }))),
  },
  {
    path: "/data-console/browser",
    name: "Data Browser",
    icon: Search,
    capability: "FIXTURE",
    navigable: true,
    component: lazy(() => import("./features/data-console/pages/BrowserLandingPage").then(m => ({ default: m.BrowserLandingPage }))),
  },
  {
    path: "/data-console/browser/:engine/:assetId",
    name: "Asset Browser",
    capability: "FIXTURE",
    navigable: false,
    component: lazy(() => import("./features/data-console/pages/AssetBrowserPage").then(m => ({ default: m.AssetBrowserPage }))),
  },
  {
    path: "/data-console/browser/:engine/:assetId/records/:recordId",
    name: "Record Detail",
    capability: "FIXTURE",
    navigable: false,
    component: lazy(() => import("./features/data-console/pages/RecordDetailPage").then(m => ({ default: m.RecordDetailPage }))),
  }
];
