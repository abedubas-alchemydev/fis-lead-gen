import { ExportClient } from "@/components/export/export-client";

export default function ExportPage({
  searchParams
}: {
  searchParams?: { list?: "primary" | "alternative" | "all" };
}) {
  return <ExportClient initialListMode={searchParams?.list} />;
}
