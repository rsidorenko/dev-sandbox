/**
 * Декоративный «aurora»-фон: медленно дрейфующие светящиеся блобы
 * и мягкий вращающийся цветовой sweep в палитре brand.
 *
 * Pure-CSS, без состояния и хуков — остаётся Server Component.
 * Анимируются только transform/opacity (GPU-composited); blur статичен.
 * Движение отключается через prefers-reduced-motion (см. globals.css).
 */
export function AuroraBackground() {
  return (
    <div
      aria-hidden
      className="pointer-events-none absolute inset-0 overflow-hidden"
    >
      {/* Дрейфующие светящиеся блобы (mix-blend-screen поверх brand-900) */}
      <div className="absolute -left-24 -top-28 h-[28rem] w-[28rem] rounded-full bg-brand-400 opacity-40 blur-3xl mix-blend-screen will-change-transform animate-aurora-a" />
      <div className="absolute -bottom-32 -right-24 h-[32rem] w-[32rem] rounded-full bg-brand-500 opacity-30 blur-3xl mix-blend-screen will-change-transform animate-aurora-b" />
      <div className="absolute left-1/2 top-1/4 h-[22rem] w-[22rem] -translate-x-1/2 rounded-full bg-brand-300 opacity-25 blur-3xl mix-blend-screen will-change-transform animate-aurora-a [animation-delay:-7s]" />

      {/* Мягкий цветовой sweep — медленно вращающийся conic-градиент */}
      <div className="absolute inset-0 animate-aurora-sweep bg-[conic-gradient(from_180deg_at_50%_50%,transparent,theme(colors.brand.400/0.18),transparent,theme(colors.brand.300/0.12),transparent)] blur-2xl" />
    </div>
  );
}
