type BrandMarkProps = {
  size?: number;
  className?: string;
};

export function BrandMark({ size = 36, className }: BrandMarkProps) {
  const fontSize = Math.round(size * 0.62);
  return (
    <div
      className={
        "grid shrink-0 place-items-center rounded-[10px] bg-navy text-white shadow-[0_6px_20px_rgba(10,31,63,0.25)] " +
        (className ?? "")
      }
      style={{
        width: size,
        height: size,
        fontSize,
        fontWeight: 800,
        letterSpacing: "-0.04em",
        lineHeight: 1,
        fontFamily:
          "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, sans-serif"
      }}
      aria-hidden
    >
      d
    </div>
  );
}
