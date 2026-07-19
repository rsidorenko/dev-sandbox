import { Suspense } from "react";
import { siteConfig } from "@/shared/config/site";
import { ReferralCapture } from "@/shared/lib/referralCapture";
import { PromoCapture } from "@/shared/lib/promoCapture";
import { HeroSection } from "@/widgets/landing/HeroSection";
import { FeaturesSection } from "@/widgets/landing/FeaturesSection";
import { HowItWorks } from "@/widgets/landing/HowItWorks";
import { TariffsSection } from "@/widgets/landing/TariffsSection";
import { DeliverySection } from "@/widgets/landing/DeliverySection";
import { FaqSection } from "@/widgets/landing/FaqSection";
import { ReferralSection } from "@/widgets/landing/ReferralSection";
import { CtaSection } from "@/widgets/landing/CtaSection";

export default function HomePage() {
  return (
    <>
      <Suspense fallback={null}>
        <ReferralCapture />
        <PromoCapture />
      </Suspense>
      <HeroSection />
      <FeaturesSection />
      <HowItWorks steps={siteConfig.howItWorks} />
      <TariffsSection />
      <ReferralSection />
      <DeliverySection />
      <FaqSection faq={siteConfig.faq} />
      <CtaSection />
    </>
  );
}
