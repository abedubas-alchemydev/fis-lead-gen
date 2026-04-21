import { FileDown } from "lucide-react";

export default function ExportPage() {
  return (
    <section className="flex min-h-[60vh] flex-col items-center justify-center gap-6 text-center">
      <div className="rounded-2xl bg-navy/10 p-5">
        <FileDown className="h-10 w-10 text-navy" />
      </div>
      <div>
        <p className="text-sm font-medium uppercase tracking-[0.24em] text-blue">Export</p>
        <h1 className="mt-2 text-3xl font-semibold text-navy">Coming Soon</h1>
        <p className="mx-auto mt-3 max-w-md text-sm leading-6 text-slate-600">
          The controlled CSV export feature is currently under development. It will include restricted row limits, field-level privacy controls, and daily export caps to keep teams working in-platform.
        </p>
      </div>
    </section>
  );
}
