import { Suspense } from "react";
import { Header } from "@/widgets/header/Header";
import { Footer } from "@/widgets/footer/Footer";
import { AuthProvider } from "@/shared/lib/auth";
import { ReferralCapture } from "@/shared/lib/referralCapture";

// Каркас ОСНОВНОГО сайта (шапка, футер, пользовательская сессия, захват ?ref=).
// Вынесен из корневого layout в route-group (site), чтобы маршрут /admin/* мог
// быть полностью отдельной страницей со своим каркасом и без меню сайта.
export default function SiteLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <AuthProvider>
      {/*
        Capture ?ref= on EVERY page (not just /) so referral links to /login,
        /payment/*, /trial, /dashboard still attribute. Suspense boundary is
        required because ReferralCapture uses useSearchParams (Next.js App Router).
      */}
      <Suspense fallback={null}>
        <ReferralCapture />
      </Suspense>
      <Header />
      <main>{children}</main>
      <Footer />
    </AuthProvider>
  );
}
