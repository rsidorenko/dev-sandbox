export function HeroSection() {
  return (
    <section className="relative overflow-hidden bg-gradient-to-br from-brand-900 via-brand-800 to-brand-950 py-24 text-white sm:py-32">
      <div className="absolute inset-0 opacity-10">
        <div className="absolute -left-20 -top-20 h-96 w-96 rounded-full bg-brand-400 blur-3xl" />
        <div className="absolute -bottom-20 -right-20 h-96 w-96 rounded-full bg-brand-300 blur-3xl" />
      </div>

      <div className="relative mx-auto max-w-4xl px-4 text-center">
        <span className="inline-block rounded-full bg-white/10 px-4 py-1.5 text-xs font-medium tracking-wide">
          Защищённый сетевой доступ
        </span>
        <h1 className="mt-6 text-4xl font-extrabold leading-tight tracking-tight sm:text-5xl lg:text-6xl">
          Безопасное соединение
          <br />
          <span className="text-brand-300">для вашего трафика</span>
        </h1>
        <p className="mx-auto mt-6 max-w-2xl text-lg leading-relaxed text-brand-100">
          Персональный защищённый сетевой туннель с шифрованием соединения.
          Защитите свой трафик при использовании публичных и ненадёжных Wi-Fi
          сетей.
        </p>
        <div className="mt-10 flex flex-col items-center gap-4 sm:flex-row sm:justify-center">
          <a
            href="#tariffs"
            className="rounded-xl bg-white px-8 py-3.5 text-sm font-bold text-brand-800 shadow-lg transition hover:bg-brand-50"
          >
            Выбрать тариф
          </a>
          <a
            href="#how-it-works"
            className="rounded-xl border border-white/20 px-8 py-3.5 text-sm font-semibold text-white transition hover:bg-white/10"
          >
            Как это работает
          </a>
        </div>
      </div>
    </section>
  );
}
