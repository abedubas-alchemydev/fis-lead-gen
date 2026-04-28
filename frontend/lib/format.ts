export function formatCurrency(value: number | null) {
  if (value === null) {
    return "N/A";
  }

  // Compact notation makes the unit visible (e.g. "$74.3M", "$1.5B") so
  // capital values can never be read as bare numbers when a column truncates.
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    notation: "compact",
    maximumFractionDigits: 1
  }).format(value);
}

export function formatPercent(value: number | null) {
  if (value === null) {
    return "N/A";
  }

  return `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
}

export function formatDate(value: string | null) {
  if (!value) {
    return "Not available";
  }

  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric"
  }).format(new Date(value));
}

export function formatRelativeTime(value: string) {
  const now = Date.now();
  const target = new Date(value).getTime();
  const deltaMs = target - now;

  const units: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ["day", 1000 * 60 * 60 * 24],
    ["hour", 1000 * 60 * 60],
    ["minute", 1000 * 60]
  ];

  for (const [unit, ms] of units) {
    if (Math.abs(deltaMs) >= ms || unit === "minute") {
      return new Intl.RelativeTimeFormat("en-US", { numeric: "auto" }).format(
        Math.round(deltaMs / ms),
        unit
      );
    }
  }

  return "just now";
}
