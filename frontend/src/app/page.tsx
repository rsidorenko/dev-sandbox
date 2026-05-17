import { siteConfig } from "@/shared/config/site";
import { HeroSection } from "@/widgets/landing/HeroSection";
import { HowItWorks } from "@/widgets/landing/HowItWorks";
import { TariffsSection } from "@/widgets/landing/TariffsSection";
import { DeliverySection } from "@/widgets/landing/DeliverySection";
import { FaqSection } from "@/widgets/landing/FaqSection";
import { CtaSection } from "@/widgets/landing/CtaSection";

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
