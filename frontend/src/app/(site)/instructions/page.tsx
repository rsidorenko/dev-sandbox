import { Metadata } from "next";
import { PageHeader } from "@/shared/ui/PageHeader";
import { InstructionsBlock } from "@/features/instructions/InstructionsBlock";

export const metadata: Metadata = {
  title: "Инструкция по подключению",
  description:
    "Пошаговая инструкция по настройке защищённого соединения на Windows, Android, iPhone, Mac и Android TV.",
};

export default function InstructionsPage() {
  return (
    <>
      <PageHeader
        title="Инструкция по подключению"
        description="Настройте защищённое соединение за несколько шагов"
      />
      <section className="mx-auto max-w-3xl px-4 py-12">
        <InstructionsBlock />
      </section>
    </>
  );
}
