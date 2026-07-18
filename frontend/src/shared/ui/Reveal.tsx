"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

type RevealProps = {
  children: ReactNode;
  /** Задержка появления (мс) — для каскада/стаггера. */
  delay?: number;
  className?: string;
};

/**
 * Переиспользуемая обёртка scroll-reveal: плавное появление контента
 * (fade-in-up) при попадании в viewport через IntersectionObserver.
 *
 * Стартовое состояние `opacity-0` детерминировано на SSR и первом
 * клиентском рендере → нет hydration-mismatch. Под prefers-reduced-motion
 * переход мгновенный (motion-reduce:duration-0), плюс страховка в
 * globals.css ([data-reveal] force visible) — контент всегда виден.
 */
export function Reveal({ children, delay = 0, className = "" }: RevealProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [shown, setShown] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setShown(true);
          io.disconnect();
        }
      },
      { threshold: 0.15, rootMargin: "0px 0px -10% 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  return (
    <div
      ref={ref}
      data-reveal
      style={{ transitionDelay: `${delay}ms` }}
      className={`transition-all duration-700 ease-out motion-reduce:duration-0 ${
        shown ? "translate-y-0 opacity-100" : "translate-y-6 opacity-0"
      } ${className}`}
    >
      {children}
    </div>
  );
}
