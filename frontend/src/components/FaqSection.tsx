"use client";

import { useState } from "react";

type FaqItem = {
  question: string;
  answer: string;
};

type Props = {
  faq: readonly FaqItem[];
};

export function FaqSection({ faq }: Props) {
  const [open, setOpen] = useState<number | null>(null);

  return (
    <section id="faq" className="py-20">
      <div className="mx-auto max-w-3xl px-4">
        <div className="text-center">
          <h2 className="text-3xl font-bold tracking-tight text-gray-900">
            Часто задаваемые вопросы
          </h2>
          <p className="mt-3 text-gray-500">
            Ответы на популярные вопросы о сервисе
          </p>
        </div>

        <div className="mt-12 flex flex-col gap-3">
          {faq.map((item, i) => (
            <div
              key={i}
              className="rounded-xl border border-gray-100 bg-white transition"
            >
              <button
                className="flex w-full items-center justify-between px-6 py-4 text-left"
                onClick={() => setOpen(open === i ? null : i)}
              >
                <span className="text-sm font-semibold text-gray-900">
                  {item.question}
                </span>
                <svg
                  className={`h-5 w-5 shrink-0 text-gray-400 transition-transform ${
                    open === i ? "rotate-180" : ""
                  }`}
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={2}
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M19 9l-7 7-7-7"
                  />
                </svg>
              </button>
              {open === i && (
                <div className="px-6 pb-4 text-sm leading-relaxed text-gray-600">
                  {item.answer}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
