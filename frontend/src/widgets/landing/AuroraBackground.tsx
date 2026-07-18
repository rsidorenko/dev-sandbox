/**
 * Декоративный «aurora»-фон: дрейфующие светящиеся блобы + мягкий
 * вращающийся цветовой sweep в палитре brand.
 *
 * Pure-CSS, без состояния и хуков — Server Component.
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
      <div className="absolute -left-24 -top-28 h-[32rem] w-[32rem] rounded-full bg-brand-400 opacity-60 blur-3xl mix-blend-screen will-change-transform animate-aurora-a" />
      <div className="absolute -bottom-40 -right-24 h-[36rem] w-[36rem] rounded-full bg-brand-500 opacity-55 blur-3xl mix-blend-screen will-change-transform animate-aurora-b" />
      <div className="absolute left-1/2 top-1/4 h-[26rem] w-[26rem] -translate-x-1/2 rounded-full bg-brand-300 opacity-50 blur-3xl mix-blend-screen will-change-transform animate-aurora-a [animation-delay:-5s]" />
      <div className="absolute right-1/4 top-1/2 h-[24rem] w-[24rem] rounded-full bg-brand-600 opacity-45 blur-3xl mix-blend-screen will-change-transform animate-aurora-b [animation-delay:-9s]" />

      {/* Мягкий цветовой sweep — медленно вращающийся conic-градиент */}
      <div className="absolute inset-0 animate-aurora-sweep bg-[conic-gradient(from_180deg_at_50%_50%,transparent,theme(colors.brand.400/0.32),transparent,theme(colors.brand.300/0.22),transparent)] blur-2xl" />
    </div>
  );
}
