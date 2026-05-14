import Link from "next/link";

type Props = {
  title: string;
  description?: string;
};

export function PageHeader({ title, description }: Props) {
  return (
    <section className="bg-gradient-to-br from-brand-50 to-white py-16">
      <div className="mx-auto max-w-3xl px-4 text-center">
        <h1 className="text-3xl font-bold tracking-tight text-gray-900 sm:text-4xl">
          {title}
        </h1>
        {description && (
          <p className="mt-4 text-lg text-gray-600">{description}</p>
        )}
        <Link
          href="/"
          className="mt-6 inline-block text-sm font-medium text-brand-600 transition hover:text-brand-700"
        >
          &larr; На главную
        </Link>
      </div>
    </section>
  );
}
