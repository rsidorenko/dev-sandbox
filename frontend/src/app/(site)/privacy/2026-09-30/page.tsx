import { PageHeader } from "@/shared/ui/PageHeader";
import { sections } from "@/shared/legal/privacy-2026-09-30";
export const metadata = { title: "Политика обработки персональных данных" };
export default function LegalPage() {
  return <><PageHeader title="Политика обработки персональных данных" description="Редакция от 30 сентября 2026 года" />
    <article className="mx-auto max-w-3xl space-y-6 px-4 py-12">
      {sections.map(section => <section key={section.title} className="rounded-2xl border border-gray-200 p-6 dark:border-zinc-700">
        <h2 className="text-lg font-semibold">{section.title}</h2>
        {section.paragraphs.map((text, i) => Array.isArray(text) ? <ul key={i} className="mt-3 list-disc space-y-2 pl-5 text-sm leading-relaxed">{text.map((item, j) => <li key={j}>{item.replace(/^- /, "")}</li>)}</ul> : <p key={i} className="mt-3 text-sm leading-relaxed">{text}</p>)}
      </section>)}
    </article></>;
}
