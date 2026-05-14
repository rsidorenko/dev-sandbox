import { siteConfig } from "@/config/site";
import { HeroSection } from "@/components/HeroSection";
import { HowItWorks } from "@/components/HowItWorks";
import { TariffsSection } from "@/components/TariffsSection";
import { DeliverySection } from "@/components/DeliverySection";
import { FaqSection } from "@/components/FaqSection";
import { CtaSection } from "@/components/CtaSection";

export default function HomePage() {
  return (
    <>
      <HeroSection />
      <HowItWorks steps={siteConfig.howItWorks} />
      <TariffsSection tariffs={siteConfig.tariffs} />
      <DeliverySection />
      <FaqSection faq={siteConfig.faq} />
      <CtaSection />
    </>
  );
}
