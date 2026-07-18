import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { Header } from "@/widgets/header/Header";
import { Footer } from "@/widgets/footer/Footer";
import { siteConfig } from "@/shared/config/site";
import { AuthProvider } from "@/shared/lib/auth";
import { metrikaInitScript, metrikaPixelUrl } from "@/shared/lib/analytics";

const inter = Inter({ subsets: ["latin", "cyrillic"] });

// Машиночитаемая идентификация оператора для поисковых систем — помогает Google
// распознать сайт как реальный бизнес (ИП Сидоренко Р.Р., ИНН/ОГРНИП) и снижает
// риск ложных флагов «мошеннический/deceptive site».
const organizationLd = {
  "@context": "https://schema.org",
  "@type": "Organization",
  name: siteConfig.siteName,
  legalName: siteConfig.ipName,
  url: siteConfig.siteUrl,
  email: siteConfig.supportEmail,
  taxID: siteConfig.inn,
  identifier: `ОГРНИП ${siteConfig.ogrnip}`,
  contactPoint: {
    "@type": "ContactPoint",
    contactType: "customer support",
    areaServed: "RU",
    availableLanguage: ["Russian"],
  },
};

const faqLd = {
  "@context": "https://schema.org",
  "@type": "FAQPage",
  mainEntity: siteConfig.faq.map((f) => ({
    "@type": "Question",
    name: f.question,
    acceptedAnswer: { "@type": "Answer", text: f.answer },
  })),
};

export const metadata: Metadata = {
  title: {
    default: `${siteConfig.siteName} — Защищённый сетевой доступ`,
    template: `%s | ${siteConfig.siteName}`,
  },
  description: siteConfig.siteDescription,
  metadataBase: new URL(siteConfig.siteUrl),
  openGraph: {
    title: `${siteConfig.siteName} — Защищённый сетевой доступ`,
    description: siteConfig.siteDescription,
    url: siteConfig.siteUrl,
    siteName: siteConfig.siteName,
    locale: "ru_RU",
    type: "website",
  },
  robots: { index: true, follow: true },
  icons: { icon: "/favicon.ico", apple: "/apple-touch-icon.png" },
  alternates: { canonical: siteConfig.siteUrl },
  // Метатег верификации Google Search Console (для Security Issues → Request Review
  // при ложном флаге Safe Browsing «мошеннический сайт»). Рендерится только если
  // env задан на этапе сборки (метаданные статичны, как NEXT_PUBLIC_YM_ID).
  verification: {
    google: process.env.NEXT_PUBLIC_GOOGLE_SITE_VERIFICATION,
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ru" suppressHydrationWarning>
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{var t=localStorage.getItem("theme");if(t==="dark"||(!t&&matchMedia("(prefers-color-scheme:dark)").matches)){document.documentElement.classList.add("dark")}}catch(e){}})()`,
          }}
        />
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(organizationLd) }}
        />
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(faqLd) }}
        />
        {metrikaInitScript() && (
          <script dangerouslySetInnerHTML={{ __html: metrikaInitScript() }} />
        )}
      </head>
      <body className={inter.className}>
        {metrikaPixelUrl() && (
          <noscript>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={metrikaPixelUrl()}
              style={{ position: "absolute", left: "-9999px" }}
              alt=""
            />
          </noscript>
        )}
        <AuthProvider>
          <Header />
          <main>{children}</main>
          <Footer />
        </AuthProvider>
      </body>
    </html>
  );
}
