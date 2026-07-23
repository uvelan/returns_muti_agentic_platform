import { type EngineType } from "../../../contracts/sources";

type EngineBadgeProps = {
  engine: EngineType;
  className?: string;
}

export function EngineBadge({ engine, className = "" }: EngineBadgeProps) {
  let bgColor = "bg-gray-100 text-gray-800";
  let label: string = engine;

  switch (engine) {
    case "SQL_SERVER":
      bgColor = "bg-blue-100 text-blue-800";
      label = "SQL Server";
      break;
    case "MONGODB":
      bgColor = "bg-green-100 text-green-800";
      label = "MongoDB";
      break;
    case "NEO4J":
      bgColor = "bg-purple-100 text-purple-800";
      label = "Neo4j";
      break;
    case "PLATFORM":
      bgColor = "bg-indigo-100 text-indigo-800";
      label = "Platform";
      break;
  }

  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${bgColor} ${className}`}>
      {label}
    </span>
  );
}
