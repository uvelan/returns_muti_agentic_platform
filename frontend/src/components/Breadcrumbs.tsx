import { useLocation, Link } from "wouter";
import { ChevronRight } from "lucide-react";
import { routes } from "../routes";

export function Breadcrumbs() {
  const [location] = useLocation();
  const segments = location.split("/").filter(Boolean);
  
  const crumbs = [];
  let currentPath = "";
  for (const segment of segments) {
    currentPath += `/${segment}`;
    const routeDef = routes.find(r => r.path === currentPath);
    if (routeDef) {
      crumbs.push({ path: routeDef.path, name: routeDef.name });
    } else {
      let decodedName = segment;
      try {
        decodedName = decodeURIComponent(segment);
      } catch {
        // ignore
      }
      crumbs.push({ path: currentPath, name: decodedName });
    }
  }

  if (crumbs.length === 0) return null;

  return (
    <nav aria-label="Breadcrumb" className="flex items-center text-sm text-slate-500 mb-4">
      <ol className="flex items-center space-x-2">
        {crumbs.map((crumb, index) => (
          <li key={crumb.path} className="flex items-center">
            {index > 0 && <ChevronRight className="h-4 w-4 mx-1 text-slate-400" aria-hidden="true" />}
            {index === crumbs.length - 1 ? (
              <span className="font-medium text-slate-900" aria-current="page">
                {crumb.name}
              </span>
            ) : (
              <Link href={crumb.path} className="hover:text-slate-700">
                {crumb.name}
              </Link>
            )}
          </li>
        ))}
      </ol>
    </nav>
  );
}
