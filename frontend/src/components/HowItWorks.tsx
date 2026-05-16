type Step = {
  step: number;
  title: string;
  description: string;
};

type Props = {
  steps: readonly Step[];
};

export function HowItWorks({ steps }: Props) {
  return (
    <section id="how-it-works" className="py-20">
      <div className="mx-auto max-w-6xl px-4">
        <div className="text-center">
          <h2 className="text-3xl font-bold tracking-tight text-gray-900 dark:text-gray-100">
            Как это работает
          </h2>
          <p className="mt-3 text-gray-500 dark:text-gray-400">
            Четыре простых шага до защищённого соединения
          </p>
        </div>

        <div className="mt-14 grid gap-8 sm:grid-cols-2 lg:grid-cols-4">
          {steps.map((s) => (
            <div key={s.step} className="relative text-center">
              <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-brand-100 text-xl font-bold text-brand-700 dark:bg-brand-900/40 dark:text-brand-300">
                {s.step}
              </div>
              <h3 className="mt-4 text-lg font-semibold text-gray-900 dark:text-gray-100">
                {s.title}
              </h3>
              <p className="mt-2 text-sm leading-relaxed text-gray-500 dark:text-gray-400">
                {s.description}
              </p>
              {s.step < steps.length && (
                <div className="absolute right-0 top-7 hidden h-0.5 w-1/2 translate-x-1/2 bg-brand-100 dark:bg-brand-900/40 lg:block" />
              )}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
