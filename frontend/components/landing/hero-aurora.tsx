// Animated aurora backdrop for the hero — pure CSS, so it stays a server
// component. Three layers: a slow gradient-mesh wash (reuses `.animate-gradient`
// → the `gradient-shift` keyframe) plus the two blurred brand orbs from the
// original hero. All decorative + non-interactive (`pointer-events-none`), and
// the gradient animation auto-stills under reduced motion via the
// `prefers-reduced-motion` guard around `.animate-gradient`.
export function HeroAurora() {
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0 -z-10 overflow-hidden">
      <div
        className="animate-gradient absolute inset-0 opacity-70"
        style={{
          background:
            "radial-gradient(60% 50% at 15% 20%, rgba(99,102,241,0.18), transparent 60%), radial-gradient(55% 45% at 85% 15%, rgba(139,92,246,0.16), transparent 60%), radial-gradient(50% 50% at 70% 80%, rgba(232,168,56,0.12), transparent 60%)",
        }}
      />
      <div className="absolute -right-40 -top-40 h-[600px] w-[600px] rounded-full bg-gradient-to-br from-blue/10 to-transparent blur-3xl" />
      <div className="absolute -bottom-20 -left-20 h-[400px] w-[400px] rounded-full bg-gradient-to-tr from-gold/10 to-transparent blur-3xl" />
    </div>
  );
}
