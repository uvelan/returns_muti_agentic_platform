type OwnershipBadgeProps = {
  owner: string;
};

export function OwnershipBadge({ owner }: OwnershipBadgeProps) {
  return (
    <span className="inline-flex items-center rounded-md bg-blue-50 px-2 py-1 text-xs font-medium text-blue-700 ring-1 ring-inset ring-blue-700/10">
      {owner}
    </span>
  );
}
